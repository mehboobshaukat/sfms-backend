from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, ConfigDict


class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    target_amount: Decimal = Field(gt=0)
    start_date: date
    target_date: date | None = None
    priority_slug: str | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    target_amount: Decimal | None = Field(default=None, gt=0)
    target_date: date | None = None
    priority_slug: str | None = None


class GoalAddSavings(BaseModel):
    amount: Decimal = Field(gt=0)
    account_slug: str
    payment_method_slug: str
    description: str = Field(min_length=1, max_length=500)
    txn_date: date = Field(default_factory=date.today)
    txn_time: time = Field(default_factory=lambda: datetime.now().time().replace(microsecond=0))
    remarks: str | None = None
    request_key: str = Field(min_length=8, max_length=80)

class GoalReleaseSavings(GoalAddSavings):
    pass


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    target_amount: Decimal
    saved_amount: Decimal
    start_date: date
    target_date: date | None
    priority_slug: str | None
    is_completed: bool
    completed_at: date | None
    created_at: datetime
    updated_at: datetime
