from typing import Protocol


class LlmGatewayProtocol(Protocol):
    async def complete(self, prompt: str, model: str, reasoning_effort: str | None) -> str: ...

    async def close(self) -> None: ...
