import os
import secrets
import stat
from pathlib import Path

import pytest
from acceptance_support.asserter_llm_gateway import ExpectedLlmGatewayFailure, assert_llm_gateway_failure

from analyze_expenses_workflow.managers.exceptions.analyze_expenses_exception import ExceptionAction
from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmCliUnavailableException
from analyze_expenses_workflow.managers.gateways.clients.coding_assistant_executable_locator import CodingAssistantExecutableLocator


def create_executable(directory: Path, executable_name: str) -> Path:
    executable_path: Path = directory / (f"{executable_name}.exe" if os.name == "nt" else executable_name)
    executable_path.write_bytes(b"")
    executable_path.chmod(executable_path.stat().st_mode | stat.S_IXUSR)
    return executable_path


def test_locate_WhenTheCliIsOnPath_ThenReturnsTheInstalledExecutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable_name: str = f"assistant-{secrets.token_hex(4)}"
    expected_executable_path: Path = create_executable(tmp_path, executable_name)
    monkeypatch.setenv("PATH", str(tmp_path))

    actual_executable_path: str = CodingAssistantExecutableLocator.locate(executable_name)

    assert Path(actual_executable_path).resolve() == expected_executable_path.resolve()


def test_locate_WhenTheCliIsNotOnPath_ThenNamesTheMissingCliAsAnInfrastructureFailure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable_name: str = f"assistant-{secrets.token_hex(4)}"
    monkeypatch.setenv("PATH", str(tmp_path))
    expected_llm_gateway_failure: ExpectedLlmGatewayFailure = ExpectedLlmGatewayFailure(
        exception_type=LlmCliUnavailableException,
        action=ExceptionAction.INFRA_ACTION_REQUIRED.value,
        reason="Coding assistant CLI is not installed or could not be started",
        message_phrases=(executable_name, "not found on PATH"),
        contextual_fields=(("Operation", "executable_resolution"), ("ExecutableName", executable_name)),
    )

    with pytest.raises(LlmCliUnavailableException) as raised_exception:
        CodingAssistantExecutableLocator.locate(executable_name)

    assert_llm_gateway_failure(raised_exception.value, expected_llm_gateway_failure)
