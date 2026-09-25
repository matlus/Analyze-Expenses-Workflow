from decimal import Decimal
from typing import final

from pydantic import BaseModel, ConfigDict, Field


@final
class BudgetSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    dining_coffee_monthly_budget: Decimal = Field(gt=0, decimal_places=2)
    shopping_monthly_budget: Decimal = Field(gt=0, decimal_places=2)
