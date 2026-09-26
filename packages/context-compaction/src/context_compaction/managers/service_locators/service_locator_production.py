from pathlib import Path

import httpx2

from context_compaction.managers.configuration_providers.configuration_provider import ConfigurationProvider
from context_compaction.managers.gateways.jev_gateway import JevGateway
from context_compaction.managers.gateways.jev_gateway_protocol import JevGatewayProtocol
from context_compaction.managers.models.context_models import JevSettings

DEFAULT_ENV_FILE = Path(".env")


class ServiceLocatorProduction:
    def __init__(self, env_file: Path = DEFAULT_ENV_FILE) -> None:
        self._env_file: Path = env_file

    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider(self._env_file)

    def make_jev_http_transport(self) -> httpx2.AsyncBaseTransport:
        return httpx2.AsyncHTTPTransport()

    def create_jev_gateway(self, jev_settings: JevSettings) -> JevGatewayProtocol:
        return JevGateway(jev_settings, async_base_transport=self.make_jev_http_transport())
