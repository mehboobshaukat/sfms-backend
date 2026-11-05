from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    loan_code: str
    loan_type: str
    contact_slug: str | None
    principal_amount: Decimal
    remaining_amount: Decimal
    is_completed: bool
    due_date: date | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
