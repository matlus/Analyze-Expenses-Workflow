import pytest

from analyze_expenses_workflow.managers.configuration_providers.settings_models.coding_assistant_settings import CodingAssistantSubscription


def pytest_addoption(parser: pytest.Parser) -> None:
    option_group: pytest.OptionGroup = parser.getgroup("workflow eval")
    option_group.addoption(
        "--workflow-subscription",
        choices=[subscription.value for subscription in CodingAssistantSubscription],
        default=None,
        help="Coding assistant subscription for the workflow's LLM steps; it selects the model family (default: from .env)",
    )
    option_group.addoption("--workflow-model", default=None, help="Model for every LLM step (needs --workflow-subscription)")
    option_group.addoption("--workflow-effort", default=None, help="Reasoning effort for every LLM step (needs --workflow-subscription)")
    option_group.addoption(
        "--workflow-setting",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Any workflow setting, applied last, for example EXPENSE_FINDINGS_ANTHROPIC_MODEL=claude-opus-5-5 or JEV_MODEL=jev-1.13",
    )
    option_group.addoption("--eval-repetitions", type=int, default=5, help="Isolated runs of the workflow (default: 5)")
    option_group.addoption("--eval-judge-model", default="sonnet", help="Model of the findings judge (default: sonnet, as in Part Three)")
    option_group.addoption("--eval-judge-effort", default="low", help="Effort of the findings judge (default: low, as in Part Three)")
