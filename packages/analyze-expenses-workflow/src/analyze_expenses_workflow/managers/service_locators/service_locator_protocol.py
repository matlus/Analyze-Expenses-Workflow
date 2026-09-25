from typing import Protocol

import httpx2

from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider


class ServiceLocatorProtocol(Protocol):
    def get_configuration_provider(self) -> ConfigurationProvider: ...

    def create_jev_http_transport(self) -> httpx2.AsyncBaseTransport | None: ...
