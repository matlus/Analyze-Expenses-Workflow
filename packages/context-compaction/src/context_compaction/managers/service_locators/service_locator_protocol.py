from typing import Protocol

from context_compaction.managers.configuration_providers.configuration_provider import ConfigurationProvider
from context_compaction.managers.gateways.jev_gateway_protocol import JevGatewayProtocol
from context_compaction.managers.models.context_models import JevSettings


class ServiceLocatorProtocol(Protocol):
    def get_configuration_provider(self) -> ConfigurationProvider: ...

    def create_jev_gateway(self, jev_settings: JevSettings) -> JevGatewayProtocol: ...
