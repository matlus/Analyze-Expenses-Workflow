from typing import Protocol

import httpx2
from copilot import CopilotClient
from openai_codex import AsyncCodex

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider


class ServiceLocatorProtocol(Protocol):
    def get_configuration_provider(self) -> ConfigurationProvider: ...

    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport | None: ...

    def create_codex_client(self) -> AsyncCodex: ...

    def create_copilot_client(self) -> CopilotClient: ...
