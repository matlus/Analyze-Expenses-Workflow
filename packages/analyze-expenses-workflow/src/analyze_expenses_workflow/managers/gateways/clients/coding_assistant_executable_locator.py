import shutil
from typing import Final, final

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmCliNotFoundException


@final
class CodingAssistantExecutableLocator:
    """Finds the coding assistant CLI the user installed and signs in with, so every subscription runs the same
    executable the user runs; an SDK's own bundled or downloaded copy can fall behind it and lose access to newer models."""

    CODEX_EXECUTABLE_NAME: Final[str] = "codex"
    COPILOT_EXECUTABLE_NAME: Final[str] = "copilot"
    _OPERATION_CONTEXT_FIELD_NAME: Final[str] = "Operation"

    @classmethod
    def locate(cls, executable_name: str) -> str:
        executable_path: str | None = shutil.which(executable_name)
        if executable_path is None:
            raise LlmCliNotFoundException(
                f"The coding assistant CLI {executable_name!r} was not found on PATH; install it and sign in to use this subscription",
                {cls._OPERATION_CONTEXT_FIELD_NAME: "executable_resolution", "ExecutableName": executable_name},
            )
        return executable_path
