from types import TracebackType
from typing import Self, cast, final

from copilot import CopilotClient
from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.client import StopError
from copilot.session import ReasoningEffort
from copilot.session_events import AssistantMessageData, SessionEvent

from analyze_expenses_workflow.managers.exceptions.llm_gateway_exception import LlmGatewayException


@final
class CopilotSubscriptionGateway:
    _SUPPORTED_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

    def __init__(self, copilot_client: CopilotClient | None = None) -> None:
        self._copilot_client: CopilotClient | None = copilot_client
        self._closed: bool = False

    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str:
        self._ensure_open()
        effort: ReasoningEffort | None = self._resolve_effort(reasoning_effort)

        try:
            session_event: SessionEvent | None = await self._request(prompt, model, effort)
        except (JsonRpcError, ProcessExitedError, StopError, RuntimeError, OSError, TimeoutError) as exc:
            raise LlmGatewayException("Copilot subscription request failed") from exc

        return self._response_text(session_event)

    def _ensure_open(self) -> None:
        if self._closed:
            raise LlmGatewayException("Copilot gateway is closed")

    @classmethod
    def _resolve_effort(cls, reasoning_effort: str | None) -> ReasoningEffort | None:
        if reasoning_effort is not None and reasoning_effort not in cls._SUPPORTED_EFFORTS:
            raise LlmGatewayException(f"Copilot does not recognize reasoning effort {reasoning_effort!r}")
        return cast("ReasoningEffort | None", reasoning_effort)

    def _client_for_request(self) -> CopilotClient:
        self._ensure_open()
        if self._copilot_client is None:
            self._copilot_client = CopilotClient()
        return self._copilot_client

    async def _request(self, prompt: str, model: str, effort: ReasoningEffort | None) -> SessionEvent | None:
        copilot_client: CopilotClient = self._client_for_request()
        async with await copilot_client.create_session(
            model=model,
            reasoning_effort=effort,
            available_tools=[],
        ) as copilot_session:
            return await copilot_session.send_and_wait(prompt)

    @staticmethod
    def _response_text(session_event: SessionEvent | None) -> str:
        if session_event is None or not isinstance(session_event.data, AssistantMessageData) or not session_event.data.content:
            raise LlmGatewayException("Copilot subscription request returned no message")
        return session_event.data.content

    async def close(self) -> None:
        if not self._closed:
            if self._copilot_client is not None:
                await self._copilot_client.stop()
            self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
