from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from dotenv import dotenv_values
from pydantic import SecretStr

from context_compaction.managers.configuration_providers.validators.validator_jev_configuration import ValidatorJevConfiguration
from context_compaction.managers.exceptions.context_compaction_error import ContextCompactionConfigurationReadError
from context_compaction.managers.models.context_models import JevSettings


class ConfigurationProvider:
    def __init__(self, env_file: Path, override_by_environment_variable_name: Mapping[str, str] | None = None) -> None:
        self._env_file: Path = env_file
        self._override_by_environment_variable_name: Mapping[str, str] = dict(override_by_environment_variable_name or {})

    def get_jev_settings(self) -> JevSettings:
        setting_value_by_environment_variable_name: Mapping[str, str | None] = self._read_environment_file()
        return self._create_jev_settings(setting_value_by_environment_variable_name)

    def _read_environment_file(self) -> Mapping[str, str | None]:
        try:
            return dict(dotenv_values(self._env_file)) if self._env_file.is_file() else {}
        except Exception as error:
            raise ContextCompactionConfigurationReadError(self._env_file) from error

    def _create_jev_settings(self, setting_value_by_environment_variable_name: Mapping[str, str | None]) -> JevSettings:
        api_key: str | None = self._setting("OPEN_ROUTER_KEY", setting_value_by_environment_variable_name)
        base_url: str | None = self._setting("OPEN_ROUTER_BASE_URL", setting_value_by_environment_variable_name)
        model: str | None = self._setting("JEV_MODEL", setting_value_by_environment_variable_name)
        ValidatorJevConfiguration.validate(api_key, base_url, model)
        return JevSettings(SecretStr(cast(str, api_key)), cast(str, base_url), cast(str, model))

    def _setting(self, environment_variable_name: str, setting_value_by_environment_variable_name: Mapping[str, str | None]) -> str | None:
        return (
            self._override_by_environment_variable_name.get(environment_variable_name)
            or os.getenv(environment_variable_name)
            or setting_value_by_environment_variable_name.get(environment_variable_name)
        )
