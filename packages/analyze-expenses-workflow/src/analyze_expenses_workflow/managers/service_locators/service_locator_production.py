from analyze_expenses_workflow.managers.configuration_providers.configuration_provider import ConfigurationProvider


class ServiceLocatorProduction:
    def get_configuration_provider(self) -> ConfigurationProvider:
        return ConfigurationProvider()
