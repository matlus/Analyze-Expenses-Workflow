import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Final, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analyze_expenses_workflow.managers.exceptions.claude_code_cli_exception import (
    ClaudeCodeCliExitedException,
    ClaudeCodeCliNotFoundException,
    ClaudeCodeCliRequestFailedException,
    ClaudeCodeCliResultInvalidException,
    ClaudeCodeCliStartFailedException,
    ClaudeCodeCliTimeoutException,
)

type ProcessTreeTerminator = Callable[[int], Awaitable[None]]

_TASKKILL_WAIT_SECONDS: Final[float] = 10.0


async def terminate_process_tree(process_id: int) -> None:
    """Ends a process together with every process it started. On Windows `claude` is usually an npm `claude.cmd` shim, so
    the CLI's node process is a grandchild that killing the shim alone would leave running."""
    if sys.platform == "win32":
        taskkill_path: Path = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
        taskkill_process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            taskkill_path,
            "/T",
            "/F",
            "/PID",
            str(process_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(taskkill_process.wait(), timeout=_TASKKILL_WAIT_SECONDS)
        except TimeoutError:
            taskkill_process.kill()
            await taskkill_process.wait()
    else:
        # The CLI starts in a session of its own, so its process group holds the CLI and everything it started.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process_id, signal.SIGKILL)


@final
class _ClaudeCodeResultResource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    subtype: str
    is_error: bool
    result_text: str | None = Field(default=None, alias="result")


@final
class ClaudeCodeCliClient:
    """Runs one prompt per `claude -p` process, isolated from the user's settings, tools, MCP servers, skills and history.
    Each process lives only for the call that starts it, so the client owns no resource between calls."""

    # --restricted ignores the user's settings files and confines the session to its working directory;
    # --strict-mcp-config without --mcp-config loads no MCP servers; --disable-slash-commands loads no skills;
    # --no-session-persistence keeps these requests out of the user's session history; --tools "" offers no tools.
    _ISOLATION_ARGUMENTS: ClassVar[tuple[str, ...]] = (
        "--restricted",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "json",
    )
    _SUCCESS_RESULT_SUBTYPE: ClassVar[str] = "success"
    _DIAGNOSTIC_TAIL_LENGTH: ClassVar[int] = 500
    _WORKING_DIRECTORY_PREFIX: ClassVar[str] = "claude-code-request-"
    _TEXT_ENCODING: ClassVar[str] = "utf-8"
    _OPERATION_CONTEXT_FIELD_NAME: ClassVar[str] = "Operation"
    _EXECUTABLE_NAME_CONTEXT_FIELD_NAME: ClassVar[str] = "ExecutableName"
    _STANDARD_ERROR_TAIL_CONTEXT_FIELD_NAME: ClassVar[str] = "StandardErrorTail"
    DEFAULT_COMMAND_PREFIX: Final[tuple[str, ...]] = ("claude",)
    DEFAULT_TIMEOUT_SECONDS: Final[float] = 900.0

    def __init__(
        self,
        command_prefix: tuple[str, ...] = DEFAULT_COMMAND_PREFIX,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        process_tree_terminator: ProcessTreeTerminator = terminate_process_tree,
    ) -> None:
        if not command_prefix:
            raise ValueError("The Claude Code command prefix must name an executable")
        self._command_prefix: tuple[str, ...] = command_prefix
        self._timeout_seconds: float = timeout_seconds
        self._process_tree_terminator: ProcessTreeTerminator = process_tree_terminator

    async def run_prompt(self, prompt: str, model: str, effort: str | None) -> str | None:
        """The result text of one successful request, or None when the result document carries no text."""
        command_arguments: tuple[str, ...] = self._compose_command_arguments(model, effort)
        with tempfile.TemporaryDirectory(prefix=self._WORKING_DIRECTORY_PREFIX, ignore_cleanup_errors=True) as working_directory:
            standard_output: bytes
            standard_error: bytes
            standard_output, standard_error = await self._run_process(command_arguments, prompt, working_directory)
        claude_code_result_resource: _ClaudeCodeResultResource = self._parse_result(standard_output)
        self._raise_if_request_failed(claude_code_result_resource, standard_error)
        return claude_code_result_resource.result_text

    def _compose_command_arguments(self, model: str, effort: str | None) -> tuple[str, ...]:
        executable_name: str = self._command_prefix[0]
        executable_path: str | None = shutil.which(executable_name)
        if executable_path is None:
            raise ClaudeCodeCliNotFoundException(
                f"The Claude Code CLI executable {executable_name!r} was not found on PATH",
                {self._OPERATION_CONTEXT_FIELD_NAME: "executable_resolution", self._EXECUTABLE_NAME_CONTEXT_FIELD_NAME: executable_name},
            )
        effort_arguments: tuple[str, ...] = ("--effort", effort) if effort is not None else ()
        return (executable_path, *self._command_prefix[1:], "-p", "--model", model, *effort_arguments, *self._ISOLATION_ARGUMENTS)

    async def _run_process(self, command_arguments: tuple[str, ...], prompt: str, working_directory: str) -> tuple[bytes, bytes]:
        process: asyncio.subprocess.Process = await self._start_process(command_arguments, working_directory)
        try:
            standard_output: bytes
            standard_error: bytes
            standard_output, standard_error = await self._communicate(process, prompt)
        finally:
            # Reached on a timeout and on cancellation, which would otherwise leave the CLI running.
            await self._stop_process(process)
        self._raise_if_exited_without_result(process, standard_output, standard_error)
        return standard_output, standard_error

    async def _communicate(self, process: asyncio.subprocess.Process, prompt: str) -> tuple[bytes, bytes]:
        try:
            return await asyncio.wait_for(process.communicate(prompt.encode(self._TEXT_ENCODING)), timeout=self._timeout_seconds)
        except TimeoutError as timeout_error:
            raise ClaudeCodeCliTimeoutException(
                f"Claude Code CLI did not finish within {self._timeout_seconds} seconds",
                {self._OPERATION_CONTEXT_FIELD_NAME: "process_wait", "TimeoutSeconds": self._timeout_seconds},
            ) from timeout_error

    def _raise_if_exited_without_result(self, process: asyncio.subprocess.Process, standard_output: bytes, standard_error: bytes) -> None:
        if process.returncode != 0 and not standard_output.strip():
            standard_error_tail: str = self._tail(standard_error)
            raise ClaudeCodeCliExitedException(
                f"Claude Code CLI exited with {process.returncode} and wrote no result document: {standard_error_tail.strip() or 'no error output'}",
                {
                    self._OPERATION_CONTEXT_FIELD_NAME: "process_exit",
                    "ExitCode": process.returncode if process.returncode is not None else "unknown",
                    self._STANDARD_ERROR_TAIL_CONTEXT_FIELD_NAME: standard_error_tail,
                },
            )

    async def _start_process(self, command_arguments: tuple[str, ...], working_directory: str) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *command_arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_directory,
                start_new_session=True,
            )
        except OSError as start_error:
            raise ClaudeCodeCliStartFailedException(
                f"Claude Code CLI could not be started: {type(start_error).__name__}",
                {self._OPERATION_CONTEXT_FIELD_NAME: "process_start", self._EXECUTABLE_NAME_CONTEXT_FIELD_NAME: self._command_prefix[0]},
            ) from start_error

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            # A failed tree kill must not replace the timeout or cancellation being handled; killing the CLI process
            # itself is the fallback, and it may already have exited by then.
            with contextlib.suppress(OSError):
                await self._process_tree_terminator(process.pid)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    @classmethod
    def _parse_result(cls, standard_output: bytes) -> _ClaudeCodeResultResource:
        try:
            return _ClaudeCodeResultResource.model_validate_json(standard_output)
        except ValidationError as validation_error:
            # The validation error quotes the raw output, which may hold model text, so only its count travels on.
            raise ClaudeCodeCliResultInvalidException(
                "Claude Code CLI wrote output that is not a result document",
                {
                    cls._OPERATION_CONTEXT_FIELD_NAME: "result_parsing",
                    "OutputByteCount": len(standard_output),
                    "ValidationErrorCount": validation_error.error_count(),
                },
            ) from None

    @classmethod
    def _raise_if_request_failed(cls, claude_code_result_resource: _ClaudeCodeResultResource, standard_error: bytes) -> None:
        # An error result (an API error, an exhausted subscription, an expired login) explains itself only in its result
        # text and standard error, so a bounded tail of each travels with the exception; the prompt never does.
        if claude_code_result_resource.is_error or claude_code_result_resource.subtype != cls._SUCCESS_RESULT_SUBTYPE:
            result_text_tail: str = (claude_code_result_resource.result_text or "")[-cls._DIAGNOSTIC_TAIL_LENGTH :]
            standard_error_tail: str = cls._tail(standard_error)
            raise ClaudeCodeCliRequestFailedException(
                f"Claude Code CLI reported a failed request ({claude_code_result_resource.subtype}): "
                f"{result_text_tail.strip() or standard_error_tail.strip() or 'no reason given'}",
                {
                    cls._OPERATION_CONTEXT_FIELD_NAME: "result_status",
                    "ResultSubtype": claude_code_result_resource.subtype,
                    "ResultTextTail": result_text_tail,
                    cls._STANDARD_ERROR_TAIL_CONTEXT_FIELD_NAME: standard_error_tail,
                },
            )

    @classmethod
    def _tail(cls, process_output: bytes) -> str:
        return process_output.decode(cls._TEXT_ENCODING, errors="replace")[-cls._DIAGNOSTIC_TAIL_LENGTH :]
