import httpx2
from copilot import CopilotClient
from openai_codex import AsyncCodex

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider


class ServiceLocatorProduction:
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider()

    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport | None:
        return None

    def create_codex_client(self) -> AsyncCodex:
        return AsyncCodex()

    def create_copilot_client(self) -> CopilotClient:
        return CopilotClient()
