from urllib.parse import SplitResult, urlsplit

from context_compaction.managers.exceptions.context_compaction_error import ContextCompactionConfigurationError


class ValidatorJevConfiguration:
    @staticmethod
    def validate(api_key: str | None, base_url: str | None, model: str | None) -> None:
        problems: list[str] = ValidatorJevConfiguration._required_settings(api_key, base_url, model)
        problems.extend(ValidatorJevConfiguration._base_url(base_url))
        if problems:
            raise ContextCompactionConfigurationError("; ".join(problems))

    @staticmethod
    def _required_settings(api_key: str | None, base_url: str | None, model: str | None) -> list[str]:
        problems: list[str] = []
        environment_variable_name: str
        setting_value: str | None
        for environment_variable_name, setting_value in (("OPEN_ROUTER_KEY", api_key), ("OPEN_ROUTER_BASE_URL", base_url), ("JEV_MODEL", model)):
            if setting_value is None or not setting_value.strip():
                problems.append(f"Set {environment_variable_name}; no Jev call was made")
        return problems

    @staticmethod
    def _base_url(base_url: str | None) -> list[str]:
        if base_url is None or not base_url.strip():
            return []
        try:
            parsed_url: SplitResult = urlsplit(base_url)
            _port_number: int | None = parsed_url.port
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.hostname
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                return ["OPEN_ROUTER_BASE_URL must be an HTTP(S) URL without embedded credentials"]
        except ValueError:
            return ["OPEN_ROUTER_BASE_URL must be a valid HTTP(S) URL"]
        return []
