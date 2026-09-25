from typing import Protocol

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider


class ServiceLocatorProtocol(Protocol):
    def get_configuration_provider(self) -> ConfigurationProvider: ...
