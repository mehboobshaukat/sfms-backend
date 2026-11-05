from datetime import date, time, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class TransactionCreate(BaseModel):
    txn_date: date
    txn_time: time
    transaction_type_slug: str
    category_slug: str
    description: str = Field(min_length=1, max_length=500)

    # Optional: nature decides which one is actually required (inflow ->
    # to_account only, outflow -> from_account only) — see
    # transaction_service.create_transaction(). Both required for
    # nature="transfer" types (Transfer, Withdraw, Deposit, or any
    # custom transfer-nature type).
    from_account_slug: str | None = None
    to_account_slug: str | None = None

    payment_method_slug: str
    contact_slug: str | None = None
    priority_slug: str
    amount: Decimal = Field(gt=0)
    charges: Decimal = Field(default=Decimal("0.00"), ge=0)
    # Only meaningful when both accounts are real (nature=transfer) and
    # charges > 0 — see Transaction.charges_borne_by on the model.
    charges_borne_by: Literal["from_account", "to_account"] = "from_account"
    reference_no: str | None = None
    remarks: str | None = None

    # Loan Repaid / Loan Collected: which existing loan this settles.
    # Loan uses its own human-readable code, not a slug.
    loan_code: str | None = None
    # Loan Received / Loan Given: optional due date for the new loan.
    due_date: date | None = None

    # Item Payment Made / Item Payment Received: which existing credit
    # purchase this settles.
    credit_purchase_slug: str | None = None


class TransactionUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=500)
    reference_no: str | None = None
    remarks: str | None = None
    priority_slug: str | None = None
    payment_method_slug: str | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_code: str
    reference_no: str | None
    txn_date: date
    txn_time: time
    transaction_type_slug: str | None
    transaction_type_label: str | None = None
    category_slug: str | None
    category_label: str | None = None
    loan_code: str | None
    goal_slug: str | None
    allocation_action: str | None
    credit_purchase_slug: str | None
    description: str
    from_account_slug: str | None
    from_account_label: str | None = None
    to_account_slug: str | None
    to_account_label: str | None = None
    payment_method_slug: str | None
    contact_slug: str | None
    contact_label: str | None = None
    priority_slug: str | None
    priority_label: str | None = None
    amount: Decimal
    charges: Decimal
    charges_borne_by: str
    net_amount: Decimal
    remarks: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TransactionFilter(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    transaction_type_slug: str | None = None
    category_slug: str | None = None
    account_slug: str | None = None
    payment_method_slug: str | None = None
    priority_slug: str | None = None
    contact_slug: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
