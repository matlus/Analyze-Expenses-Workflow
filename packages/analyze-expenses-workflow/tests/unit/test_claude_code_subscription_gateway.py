import json
import secrets
import sys
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_llm_gateway_failure

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.claude_code_cli_exception import (
    ClaudeCodeCliExitedException,
    ClaudeCodeCliRequestFailedException,
    ClaudeCodeCliResultInvalidException,
    ClaudeCodeCliStartFailedException,
    ClaudeCodeCliTimeoutException,
)
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import (
    LlmCliUnavailableException,
    LlmRequestFailedException,
    LlmResponseInvalidException,
)
from analyze_expenses_workflow.managers.gateways.clients.claude_code_cli_client import ClaudeCodeCliClient
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


def _create_claude_code_cli_client(tmp_path: Path, behavior: str, timeout_seconds: float = 60.0) -> ClaudeCodeCliClient:
    fake_claude_cli_path: Path = tmp_path / "fake_claude_cli.py"
    fake_claude_cli_path.write_text(_FAKE_CLAUDE_CLI_SOURCE, encoding="utf-8")
    return ClaudeCodeCliClient((sys.executable, str(fake_claude_cli_path), behavior), timeout_seconds=timeout_seconds)


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
    assert (
        actual_echo.get("arguments"),
        actual_echo.get("prompt"),
        Path(str(actual_echo.get("working_directory"))).name.startswith(expected_working_directory_prefix),
    ) == (expected_arguments, expected_prompt, True)


@pytest.mark.asyncio
async def test_run_prompt_WhenCliExitsWithErrorAndNoResult_ThenReportsTheExitCodeAndStandardError(tmp_path: Path) -> None:
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliExitedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code CLI exited with an error and no result document",
        message_phrases=("exited with 3", "authentication required"),
        contextual_fields=(("Operation", "process_exit"), ("ExitCode", 3), ("StandardErrorTail", "authentication required")),
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
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=ClaudeCodeCliRequestFailedException,
        action=ExceptionAction.RETRY_ACTION_NEEDED.value,
        reason="Claude Code reported a failed request",
        message_phrases=("failed request", "API Error: usage limit"),
        contextual_fields=(
            ("Operation", "result_status"),
            ("ResultSubtype", "success"),
            ("ResultTextTail", "API Error: usage limit"),
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
        message_phrases=("Claude Code", "request failed", expected_cli_failure_message),
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
async def test_complete_WhenClaudeCodeReturnsNoText_ThenReportsAnInvalidResponseWithModelAndEffort() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_default_reasoning_effort: str = "default"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseInvalidException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant response violates its contract",
        message_phrases=("Claude Code", "no response text"),
        contextual_fields=(("Operation", "response_validation"), ("Model", expected_model), ("ReasoningEffort", expected_default_reasoning_effort)),
        forbidden_message_phrases=(expected_prompt,),
    )
    claude_code_cli_client_mock: MagicMock = _create_claude_code_cli_client_mock(result_text=None)
    llm_gateway_claude_code_subscription: LlmGatewayClaudeCodeSubscription = LlmGatewayClaudeCodeSubscription(
        cast("ClaudeCodeCliClient", claude_code_cli_client_mock)
    )

    with pytest.raises(LlmResponseInvalidException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, None)

    actual_llm_response_invalid_exception: LlmResponseInvalidException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_invalid_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeCannotStart_ThenReportsAnUnavailableCliForInfrastructureAction() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"The Claude Code executable was not found on PATH {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmCliUnavailableException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Coding assistant CLI is not installed or could not be started",
        message_phrases=("Claude Code is unavailable", expected_cli_failure_message),
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

    with pytest.raises(LlmCliUnavailableException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_cli_unavailable_exception: LlmCliUnavailableException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_cli_unavailable_exception, expected_llm_gateway_failure)


@pytest.mark.asyncio
async def test_complete_WhenClaudeCodeOutputIsNotAResultDocument_ThenReportsAnInvalidResponseForDeveloperAction() -> None:
    expected_prompt: str = f"Extract expense {secrets.token_hex(8)}"
    expected_model: str = f"claude-{secrets.token_hex(8)}"
    expected_reasoning_effort: str = "low"
    expected_cli_failure_message: str = f"Claude Code wrote output that is not a result document {secrets.token_hex(4)}"
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmResponseInvalidException,
        action=ExceptionAction.DEVELOPER_ACTION_REQUIRED.value,
        reason="Coding assistant response violates its contract",
        message_phrases=("unreadable result", expected_cli_failure_message),
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

    with pytest.raises(LlmResponseInvalidException) as raised_exception:
        await llm_gateway_claude_code_subscription.complete(expected_prompt, expected_model, expected_reasoning_effort)

    actual_llm_response_invalid_exception: LlmResponseInvalidException = raised_exception.value
    assert_llm_gateway_failure(actual_llm_response_invalid_exception, expected_llm_gateway_failure)
