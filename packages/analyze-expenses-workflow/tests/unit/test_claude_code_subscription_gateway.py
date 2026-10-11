import asyncio
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_llm_gateway_failure

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.claude_code_cli_exception import (
    ClaudeCodeCliExitedException,
    ClaudeCodeCliNotFoundException,
    ClaudeCodeCliRequestFailedException,
    ClaudeCodeCliResultInvalidException,
    ClaudeCodeCliStartFailedException,
    ClaudeCodeCliTimeoutException,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmCliExitedException,
    LlmCliNotFoundException,
    LlmCliStartFailedException,
    LlmRequestFailedException,
    LlmRequestTimedOutException,
    LlmResponseEmptyException,
    LlmResponseUnreadableException,
)
from analyze_expenses_workflow.managers.gateways.clients.claude_code_cli_client import (
    ClaudeCodeCliClient,
    ProcessTreeTerminator,
    terminate_process_tree,
)
from analyze_expenses_workflow.managers.gateways.llm_gateway_claude_code_subscription import LlmGatewayClaudeCodeSubscription

# A stand-in for the `claude` executable: by default it echoes its arguments, standard input and working directory as
# the result text; the first argument after the script names a failure to act out instead.
_FAKE_CLAUDE_CLI_SOURCE: str = """
import json, os, sys, time
behavior = sys.argv[1]
prompt = sys.stdin.read()
if behavior == "exit-with-error":
    sys.stderr.write("authentication required")
    sys.exit(3)
if behavior == "hang":
    time.sleep(60)
if behavior == "not-json":
    sys.stdout.write("this is not a result document")
    sys.exit(0)
if behavior == "error-result":
    sys.stderr.write("usage limit reached")
    sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "API Error: usage limit"}))
    sys.exit(1)
echo = {"arguments": sys.argv[2:], "prompt": prompt, "working_directory": os.getcwd()}
sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(echo)}))
"""
_FAKE_CLAUDE_CLI_FILE_NAME: str = "fake_claude_cli.py"
_ISOLATION_ARGUMENTS: tuple[str, ...] = (
    "--restricted",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--tools",
    "",
    "--output-format",
    "json",
)


def _create_claude_code_cli_client(
    tmp_path: Path,
    behavior: str,
    timeout_seconds: float = 60.0,
    process_tree_terminator: ProcessTreeTerminator = terminate_process_tree,
) -> ClaudeCodeCliClient:
    fake_claude_cli_path: Path = tmp_path / _FAKE_CLAUDE_CLI_FILE_NAME
    fake_claude_cli_path.write_text(_FAKE_CLAUDE_CLI_SOURCE, encoding="utf-8")
    return ClaudeCodeCliClient(
        (sys.executable, str(fake_claude_cli_path), behavior), timeout_seconds=timeout_seconds, process_tree_terminator=process_tree_terminator
    )


def _create_unstartable_executable(directory: Path) -> Path:
    """An empty file that the operating system finds on the path but refuses to run."""
    unstartable_executable_path: Path = directory / ("unstartable-claude.exe" if os.name == "nt" else "unstartable-claude")
    unstartable_executable_path.write_bytes(b"")
    unstartable_executable_path.chmod(unstartable_executable_path.stat().st_mode | stat.S_IXUSR)
    return unstartable_executable_path


def _create_claude_code_cli_client_mock(result_text: str | None = None, failure: Exception | None = None) -> MagicMock:
    claude_code_cli_client_mock: MagicMock = MagicMock(spec=ClaudeCodeCliClient)
    claude_code_cli_client_mock.run_prompt = AsyncMock(return_value=result_text, side_effect=failure)
    return claude_code_cli_client_mock


@pytest.mark.asyncio
async def test_run_prompt_WhenCliSucceeds_ThenReturnsResultTextFromAnIsolatedSession(tmp_path: Path) -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_effort: str = "high"
    expected_arguments: list[str] = ["-p", "--model", expected_model, "--effort", expected_effort, *_ISOLATION_ARGUMENTS]
    expected_working_directory_prefix: str = "claude-code-request-"
    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(tmp_path, "echo")

    actual_result_text: str | None = await claude_code_cli_client.run_prompt(expected_prompt, expected_model, expected_effort)

    actual_echo: dict[str, object] = json.loads(actual_result_text or "{}")
    actual_working_directory_name: str = Path(str(actual_echo.get("working_directory"))).name
    assert (
        actual_echo.get("arguments"),
        actual_echo.get("prompt"),
        actual_working_directory_name.startswith(expected_working_directory_prefix),
    ) == (expected_arguments, expected_prompt, True), (
        f"Claude Code CLI request for model {expected_model!r} and effort {expected_effort!r} must run in an isolated session: "
        f"expected arguments {expected_arguments!r}, prompt {expected_prompt!r} and a working directory named with prefix "
        f"{expected_working_directory_prefix!r}; actual arguments {actual_echo.get('arguments')!r}, "
        f"prompt {actual_echo.get('prompt')!r} and working directory {actual_working_directory_name!r}"
    )


@pytest.mark.asyncio
async def test_run_prompt_WhenCliIsNotOnPath_ThenReportsTheMissingExecutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable_name: str = f"claude-{secrets.token_hex(4)}"
    monkeypatch.setenv("PATH", str(tmp_path))
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliNotFoundException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Claude Code CLI executable was not found on PATH",
        message_phrases=("Claude Code CLI", executable_name, "not found on PATH"),
        contextual_fields=(("Operation", "executable_resolution"), ("ExecutableName", executable_name)),
    )
    claude_code_cli_client: ClaudeCodeCliClient = ClaudeCodeCliClient((executable_name,))

    with pytest.raises(ClaudeCodeCliNotFoundException) as raised_exception:
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_not_found_exception: ClaudeCodeCliNotFoundException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_not_found_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_run_prompt_WhenTheExecutableCannotBeStarted_ThenReportsTheStartFailure(tmp_path: Path) -> None:
    unstartable_executable_path: Path = _create_unstartable_executable(tmp_path)
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliStartFailedException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Claude Code CLI could not be started",
        message_phrases=("Claude Code CLI", "could not be started"),
        contextual_fields=(("Operation", "process_start"), ("ExecutableName", str(unstartable_executable_path))),
        cause_type="OSError",
    )
    claude_code_cli_client: ClaudeCodeCliClient = ClaudeCodeCliClient((str(unstartable_executable_path),))

    with pytest.raises(ClaudeCodeCliStartFailedException) as raised_exception:
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_start_failed_exception: ClaudeCodeCliStartFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_start_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_run_prompt_WhenCliExitsWithErrorAndNoResult_ThenReportsTheExitCodeAndStandardError(tmp_path: Path) -> None:
    expected_standard_error_tail: str = "authentication required"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliExitedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code CLI exited with an error and no result document",
        message_phrases=("exited with 3", expected_standard_error_tail),
        contextual_fields=(("Operation", "process_exit"), ("ExitCode", 3), ("StandardErrorTail", expected_standard_error_tail)),
    )
    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(tmp_path, "exit-with-error")

    with pytest.raises(ClaudeCodeCliExitedException) as raised_exception:
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_exited_exception: ClaudeCodeCliExitedException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_exited_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_run_prompt_WhenCliDoesNotFinishInTime_ThenReportsTheTimeLimit(tmp_path: Path) -> None:
    timeout_seconds: float = 1.0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliTimeoutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code CLI did not finish within its time limit",
        message_phrases=(f"did not finish within {timeout_seconds} seconds",),
        contextual_fields=(("Operation", "process_wait"), ("TimeoutSeconds", timeout_seconds)),
        cause_type="TimeoutError",
    )
    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(tmp_path, "hang", timeout_seconds=timeout_seconds)

    with pytest.raises(ClaudeCodeCliTimeoutException) as raised_exception:
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_timeout_exception: ClaudeCodeCliTimeoutException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_timeout_exception, expected_llm_gateway_failure)


def _record_started_cli_processes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The ids of the fake CLI processes the client starts; a process tree terminator's own helper processes are left out."""
    started_process_ids: list[int] = []
    create_subprocess_exec = asyncio.create_subprocess_exec

    async def create_recorded_subprocess_exec(*command_arguments: str | Path, **process_options: object) -> asyncio.subprocess.Process:
        process: asyncio.subprocess.Process = await create_subprocess_exec(*command_arguments, **process_options)
        if any(str(command_argument).endswith(_FAKE_CLAUDE_CLI_FILE_NAME) for command_argument in command_arguments):
            started_process_ids.append(process.pid)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_recorded_subprocess_exec)
    return started_process_ids


@pytest.mark.asyncio
async def test_run_prompt_WhenCliDoesNotFinishInTime_ThenEndsTheCliProcessTree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    timeout_seconds: float = 1.0
    # Filled with the id of the CLI process the client starts, the one process whose tree the timeout must end.
    expected_terminated_process_ids: list[int] = _record_started_cli_processes(monkeypatch)
    actual_terminated_process_ids: list[int] = []

    async def record_process_tree_termination(process_id: int) -> None:
        actual_terminated_process_ids.append(process_id)
        await terminate_process_tree(process_id)

    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(
        tmp_path, "hang", timeout_seconds=timeout_seconds, process_tree_terminator=record_process_tree_termination
    )

    with pytest.raises(ClaudeCodeCliTimeoutException):
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    assert actual_terminated_process_ids == expected_terminated_process_ids, (
        "A timed-out Claude Code CLI must have exactly its own process tree ended; "
        f"expected process ids {expected_terminated_process_ids}; actual {actual_terminated_process_ids}"
    )


@pytest.mark.asyncio
async def test_run_prompt_WhenTheProcessTreeCannotBeEnded_ThenStillReportsTheTimeLimit(tmp_path: Path) -> None:
    timeout_seconds: float = 1.0
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliTimeoutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code CLI did not finish within its time limit",
        message_phrases=(f"did not finish within {timeout_seconds} seconds",),
        contextual_fields=(("Operation", "process_wait"), ("TimeoutSeconds", timeout_seconds)),
        cause_type="TimeoutError",
    )

    async def fail_process_tree_termination(process_id: int) -> None:
        raise OSError(f"taskkill could not end process {process_id}")

    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(
        tmp_path, "hang", timeout_seconds=timeout_seconds, process_tree_terminator=fail_process_tree_termination
    )
    # The fake CLI sleeps for 60 seconds, so only the fallback kill of the CLI process ends the request well before then.
    fallback_deadline_seconds: float = 15.0

    with pytest.raises(ClaudeCodeCliTimeoutException) as raised_exception:
        async with asyncio.timeout(fallback_deadline_seconds):
            await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_timeout_exception: ClaudeCodeCliTimeoutException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_timeout_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_run_prompt_WhenCliOutputIsNotAResultDocument_ThenReportsTheOutputSize(tmp_path: Path) -> None:
    malformed_output: str = "this is not a result document"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliResultInvalidException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Claude Code CLI wrote output that is not a result document",
        message_phrases=("not a result document",),
        contextual_fields=(("Operation", "result_parsing"), ("OutputByteCount", len(malformed_output)), ("ValidationErrorCount", 1)),
        forbidden_message_phrases=(malformed_output,),
    )
    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(tmp_path, "not-json")

    with pytest.raises(ClaudeCodeCliResultInvalidException) as raised_exception:
        await claude_code_cli_client.run_prompt(secrets.token_hex(8), f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_result_invalid_exception: ClaudeCodeCliResultInvalidException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_result_invalid_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_run_prompt_WhenCliReportsAnErrorResult_ThenCarriesTheResultTextAndStandardError(tmp_path: Path) -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_result_text_tail: str = "API Error: usage limit"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code reported a failed request",
        message_phrases=("failed request", expected_result_text_tail),
        contextual_fields=(
            ("Operation", "result_status"),
            ("ResultSubtype", "success"),
            ("ResultTextTail", expected_result_text_tail),
            ("StandardErrorTail", "usage limit reached"),
        ),
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client: ClaudeCodeCliClient = _create_claude_code_cli_client(tmp_path, "error-result")

    with pytest.raises(ClaudeCodeCliRequestFailedException) as raised_exception:
        await claude_code_cli_client.run_prompt(expected_prompt, f"claude-{secrets.token_hex(8)}", None)

    actual_claude_code_cli_request_failed_exception: ClaudeCodeCliRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_claude_code_cli_request_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeRequestSucceeds_ThenReturnsResponseAndForwardsSettings() -> None:
    expected_response: str = secrets.token_hex(8)
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "xhigh"
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(result_text=expected_response)
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    actual_response: str = await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    assert actual_response == expected_response
    claude_code_cli_client_mock.run_prompt.assert_awaited_once_with(expected_prompt, expected_model, expected_reasoning_effort)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCliFails_ThenTranslatesFailureWithModelAndEffort() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code reported a failed request {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request failed",
        message_phrases=("Claude Code CLI", "request failed", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliRequestFailedException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(
        failure=ClaudeCodeCliRequestFailedException(expected_cli_failure_message)
    )
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmRequestFailedException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_request_failed_exception: LlmRequestFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeReturnsNoText_ThenReportsAnEmptyResponseWithModelAndEffort() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_default_reasoning_effort: str = "default"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseEmptyException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant returned no response text",
        message_phrases=("Claude Code CLI", "no response text"),
        contextual_fields=(("Operation", "response_validation"), ("Model", expected_model), ("ReasoningEffort", expected_default_reasoning_effort)),
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(result_text=None)
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmResponseEmptyException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_response_empty_exception: LlmResponseEmptyException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_empty_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCliIsNotInstalled_ThenReportsAnUnavailableCliForInfrastructureAction() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"The Claude Code CLI executable was not found on PATH {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmCliNotFoundException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Coding assistant CLI is not installed",
        message_phrases=("Claude Code CLI is not installed", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliNotFoundException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(failure=ClaudeCodeCliNotFoundException(expected_cli_failure_message))
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmCliNotFoundException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_cli_not_found_exception: LlmCliNotFoundException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_cli_not_found_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCliCannotBeStarted_ThenReportsAStartFailureForInfrastructureAction() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code CLI could not be started: PermissionError {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmCliStartFailedException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Coding assistant CLI is installed but could not be started",
        message_phrases=("Claude Code CLI could not be started", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliStartFailedException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(
        failure=ClaudeCodeCliStartFailedException(expected_cli_failure_message)
    )
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmCliStartFailedException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_cli_start_failed_exception: LlmCliStartFailedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_cli_start_failed_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCliTimesOut_ThenReportsATimedOutRequestForRetry() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code CLI did not finish within 60.0 seconds {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmRequestTimedOutException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant request did not finish within its time limit",
        message_phrases=("Claude Code CLI request timed out", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliTimeoutException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(failure=ClaudeCodeCliTimeoutException(expected_cli_failure_message))
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmRequestTimedOutException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_request_timed_out_exception: LlmRequestTimedOutException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_request_timed_out_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCliExitsWithoutAResult_ThenReportsTheExitForRetry() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code CLI exited with 3: authentication required {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmCliExitedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Coding assistant CLI exited with an error and no result",
        message_phrases=("Claude Code CLI exited without a result", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliExitedException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(failure=ClaudeCodeCliExitedException(expected_cli_failure_message))
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmCliExitedException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_cli_exited_exception: LlmCliExitedException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_cli_exited_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeOutputIsNotAResultDocument_ThenReportsAnUnreadableResponseForDeveloperAction() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code CLI wrote output that is not a result document {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseUnreadableException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant response could not be read",
        message_phrases=("Claude Code CLI", "unreadable result", expected_cli_failure_message),
        contextual_fields=(("Operation", "cli_request"), ("Model", expected_model), ("ReasoningEffort", expected_reasoning_effort)),
        cause_type="ClaudeCodeCliResultInvalidException",
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(
        failure=ClaudeCodeCliResultInvalidException(expected_cli_failure_message)
    )
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmResponseUnreadableException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_response_unreadable_exception: LlmResponseUnreadableException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_unreadable_exception, expected_llm_gateway_failure)
