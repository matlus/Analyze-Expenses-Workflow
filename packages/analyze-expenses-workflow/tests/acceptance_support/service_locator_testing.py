from collections.abc import Mapping
from typing import override

import httpx2

from acceptance_support.mediator_jev import TestMediatorJev
from analyze_expenses_workflow import DomainFacade
from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider
from analyze_expenses_workflow.managers.service_locators.service_locator_production import ServiceLocatorProduction


class ServiceLocatorTesting(ServiceLocatorProduction):
    def __init__(
        self,
        test_mediator_jev: TestMediatorJev,
        override_by_environment_variable_name: Mapping[str, str] | None = None,
    ) -> None:
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev
        self._override_by_environment_variable_name: Mapping[str, str] | None = override_by_environment_variable_name

    @override
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(self._override_by_environment_variable_name)

    @override
    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return self._test_mediator_jev.create_http_transport()


def create_domain_facade(
    test_mediator_jev: TestMediatorJev,
    override_by_environment_variable_name: Mapping[str, str] | None = None,
) -> DomainFacade:
    return DomainFacade(ServiceLocatorTesting(test_mediator_jev, override_by_environment_variable_name))
