import httpx2
from copilot import CopilotClient, RuntimeConnection
from openai_codex import AsyncCodex, CodexConfig

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.gateways.clients.claude_code_cli_client import ClaudeCodeCliClient
from analyze_expenses_workflow.managers.gateways.clients.coding_assistant_executable_locator import CodingAssistantExecutableLocator


class ServiceLocatorProduction:
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider()

    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport | None:
        return None

    def create_codex_client(self) -> AsyncCodex:
        codex_executable_path: str = CodingAssistantExecutableLocator.locate(CodingAssistantExecutableLocator.CODEX_EXECUTABLE_NAME)
        return AsyncCodex(CodexConfig(codex_bin=codex_executable_path))

    def create_copilot_client(self) -> CopilotClient:
        copilot_executable_path: str = CodingAssistantExecutableLocator.locate(CodingAssistantExecutableLocator.COPILOT_EXECUTABLE_NAME)
        return CopilotClient(connection=RuntimeConnection.for_stdio(path=copilot_executable_path))

    def create_claude_code_cli_client(self) -> ClaudeCodeCliClient:
        return ClaudeCodeCliClient()
