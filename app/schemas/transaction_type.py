from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class TransactionTypeBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    nature: Literal["inflow", "outflow", "transfer"]
    icon: str | None = None
    color: str | None = None


class TransactionTypeCreate(TransactionTypeBase):
    pass


class TransactionTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    nature: Literal["inflow", "outflow", "transfer"] | None = None
    icon: str | None = None
    color: str | None = None
    is_active: bool | None = None


class TransactionTypeOut(TransactionTypeBase):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
