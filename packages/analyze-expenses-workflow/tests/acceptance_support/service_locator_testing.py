import secrets
from collections.abc import Mapping
from typing import final, override

import httpx2
from copilot import CopilotClient
from openai_codex import AsyncCodex

from acceptance_support.mediator_jev import TestMediatorJev
from analyze_expenses_workflow import DomainFacade
from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import CodingAssistantSubscription
from analyze_expenses_workflow.managers.service_locators.service_locator_production import ServiceLocatorProduction


@final
class ServiceLocatorTesting(ServiceLocatorProduction):
    def __init__(
        self,
        test_mediator_jev: TestMediatorJev,
        override_by_environment_variable_name: Mapping[str, str] | None = None,
        async_codex: AsyncCodex | None = None,
        copilot_client: CopilotClient | None = None,
    ) -> None:
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev
        self._override_by_environment_variable_name: Mapping[str, str] | None = override_by_environment_variable_name
        self._async_codex: AsyncCodex | None = async_codex
        self._copilot_client: CopilotClient | None = copilot_client

    @override
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(self._override_by_environment_variable_name)

    @override
    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return self._test_mediator_jev.create_http_transport()

    @override
    def create_codex_client(self) -> AsyncCodex:
        return self._async_codex if self._async_codex is not None else super().create_codex_client()

    @override
    def create_copilot_client(self) -> CopilotClient:
        return self._copilot_client if self._copilot_client is not None else super().create_copilot_client()


def create_domain_facade(
    test_mediator_jev: TestMediatorJev,
    override_by_environment_variable_name: Mapping[str, str] | None = None,
    async_codex: AsyncCodex | None = None,
    copilot_client: CopilotClient | None = None,
) -> DomainFacade:
    return DomainFacade(ServiceLocatorTesting(test_mediator_jev, override_by_environment_variable_name, async_codex, copilot_client))


def create_subscription_test_configuration(coding_assistant_subscription: CodingAssistantSubscription, expected_model: str) -> dict[str, str]:
    return {
        "CODING_ASSISTANT_SUBSCRIPTION": coding_assistant_subscription.value,
        "OPEN_ROUTER_BASE_URL": "https://example.invalid/api",
        "OPEN_ROUTER_KEY": secrets.token_hex(16),
        "JEV_MODEL": f"test-jev-{secrets.token_hex(8)}",
        "EXPENSE_LINE_EXTRACTION_OPENAI_MODEL": expected_model,
        "EXPENSE_FINDINGS_OPENAI_MODEL": expected_model,
        "EXPENSE_CATEGORIZATION_OPENAI_MODEL": expected_model,
    }
