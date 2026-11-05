from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class PaymentMethodCreate(BaseModel):
    account_slug: str
    name: str = Field(min_length=1, max_length=100)


class PaymentMethodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class PaymentMethodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    account_slug: str | None
    account_label: str | None = None
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
