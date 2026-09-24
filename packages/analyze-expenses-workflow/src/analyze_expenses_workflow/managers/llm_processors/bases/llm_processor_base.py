import logging
import re
from collections.abc import Awaitable, Callable
from typing import ClassVar, Literal

from analyze_expenses_workflow.managers.configuration_providers.settings_models.llm_operation_settings import LlmOperationSettings
from analyze_expenses_workflow.managers.gateways.llm_gateway_protocol import LlmGatewayProtocol

ProcessingEventKind = Literal["progress", "complete"]
ProcessingEventCallback = Callable[[ProcessingEventKind, str], Awaitable[None]]


class LlmProcessorBase:
    _FENCED_JSON_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\A```(?:json)?[ \t]*\r?\n(?P<payload>.*)\r?\n```[ \t]*\Z", re.DOTALL | re.IGNORECASE
    )

    def __init__(
        self,
        gateway: LlmGatewayProtocol,
        settings: LlmOperationSettings,
        event_callback: ProcessingEventCallback | None = None,
    ) -> None:
        self._gateway: LlmGatewayProtocol = gateway
        self._settings: LlmOperationSettings = settings
        self._event_callback: ProcessingEventCallback | None = event_callback

    async def _complete(self, prompt: str) -> str:
        effort: str | None = self._settings.reasoning_effort.value if self._settings.reasoning_effort is not None else None
        return await self._gateway.complete(prompt, self._settings.model, effort)

    @classmethod
    def _json_response_text(cls, response_text: str) -> str:
        stripped_text: str = response_text.strip()
        fenced_match: re.Match[str] | None = cls._FENCED_JSON_PATTERN.fullmatch(stripped_text)
        return fenced_match.group("payload") if fenced_match is not None else stripped_text

    async def _publish(self, kind: ProcessingEventKind, message: str) -> None:
        if self._event_callback is not None:
            await self._event_callback(kind, message)
        else:
            logging.getLogger(__name__).info("%s: %s", kind, message)
