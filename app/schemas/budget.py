from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class BudgetCreate(BaseModel):
    category_slug: str
    period: Literal["weekly", "monthly", "yearly"]
    amount: Decimal = Field(gt=0)
    start_date: date
    end_date: date | None = None


class BudgetUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)
    end_date: date | None = None
    is_active: bool | None = None


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    category_slug: str | None
    period: str
    amount: Decimal
    spent_amount: Decimal
    start_date: date
    end_date: date | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
