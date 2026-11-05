from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class CreditPurchaseCreate(BaseModel):
    # "received" = you got the item, you owe money (liability).
    # "given"    = you handed over the item, you're owed money (receivable).
    credit_type: Literal["received", "given"]
    contact_slug: str
    category_slug: str | None = None
    item_description: str = Field(min_length=1, max_length=500)
    total_amount: Decimal = Field(gt=0)
    purchase_date: date
    due_date: date | None = None


class CreditPurchaseUpdate(BaseModel):
    """
    Deliberately does NOT allow changing credit_type, contact, or
    total_amount — those anchor remaining_amount and any settlement
    transactions already recorded against this purchase. To fix a wrong
    amount/direction/contact, delete (if unsettled) and re-record instead.
    """
    category_slug: str | None = None
    item_description: str | None = Field(default=None, min_length=1, max_length=500)
    due_date: date | None = None


class CreditPurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    credit_type: str
    contact_slug: str | None
    category_slug: str | None
    item_description: str
    total_amount: Decimal
    remaining_amount: Decimal
    purchase_date: date
    due_date: date | None
    is_completed: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
