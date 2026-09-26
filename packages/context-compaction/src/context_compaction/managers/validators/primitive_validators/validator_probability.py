import math


class ValidatorProbability:
    @staticmethod
    def collect_problems(probability: object) -> list[str]:
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            return [f"Expected a finite probability from zero to one; received {probability!r}."]
        return []


class ValidatorRepeatCount:
    @staticmethod
    def collect_problems(repeat_count: object, maximum_repeat_count: int) -> list[str]:
        if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or not 0 <= repeat_count <= maximum_repeat_count:
            return [f"Expected a whole-number repeat count from zero to {maximum_repeat_count}; received {repeat_count!r}."]
        return []
