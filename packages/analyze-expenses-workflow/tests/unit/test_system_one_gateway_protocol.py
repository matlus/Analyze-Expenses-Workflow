import pytest
from pydantic import ValidationError

from analyze_expenses_workflow.managers.gateways.system_one_gateway_protocol import ChoiceDecision


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("inf"), float("nan")])
def test_choice_decision_rejects_invalid_probabilities(probability: float) -> None:
    with pytest.raises(ValidationError):
        ChoiceDecision(choice="groceries", confidence=0.8, probabilities={"groceries": probability})


def test_choice_decision_rejects_a_choice_outside_the_scored_options() -> None:
    with pytest.raises(ValidationError):
        ChoiceDecision(choice="groceries", confidence=0.8, probabilities={"dining": 1.0})


def test_choice_decision_preserves_explicit_choice_after_independent_scoring() -> None:
    decision: ChoiceDecision = ChoiceDecision(
        choice="groceries", confidence=0.8, probabilities={"groceries": 0.3, "dining": 0.7}
    )

    assert decision.choice == "groceries"
    assert decision.probabilities["dining"] > decision.probabilities["groceries"]
