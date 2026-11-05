from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    account_type: Literal["cash", "digital_wallet", "bank"]
    account_number: str | None = None
    bank_name: str | None = None
    branch: str | None = None
    current_balance: Decimal = Decimal("0.00")


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    account_number: str | None = None
    bank_name: str | None = None
    branch: str | None = None
    is_active: bool | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    account_type: str
    account_number: str | None
    bank_name: str | None
    branch: str | None
    current_balance: Decimal
    is_default: bool
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
