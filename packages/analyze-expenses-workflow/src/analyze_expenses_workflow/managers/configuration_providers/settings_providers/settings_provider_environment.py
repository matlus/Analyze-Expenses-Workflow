import os
from collections.abc import Mapping

from dotenv import dotenv_values, find_dotenv


class SettingsProviderEnvironment:
    def __init__(self, override_by_environment_variable_name: Mapping[str, str] | None = None) -> None:
        self._configuration_value_by_environment_variable_name: dict[str, str] = self._load_settings(override_by_environment_variable_name)

    @classmethod
    def _load_settings(cls, override_by_environment_variable_name: Mapping[str, str] | None) -> dict[str, str]:
        return {
            **cls._load_dotenv_settings(),
            **os.environ,
            **(override_by_environment_variable_name or {}),
        }

    @staticmethod
    def _load_dotenv_settings() -> dict[str, str]:
        dotenv_path: str = find_dotenv(usecwd=True)
        dotenv_configuration_value_by_environment_variable_name: dict[str, str] = {
            environment_variable_name: configuration_setting_value
            for environment_variable_name, configuration_setting_value in dotenv_values(dotenv_path).items()
            if configuration_setting_value is not None
        }
        return dotenv_configuration_value_by_environment_variable_name

    def read_setting_value(self, environment_variable_name: str) -> str | None:
        return self._configuration_value_by_environment_variable_name.get(environment_variable_name)
