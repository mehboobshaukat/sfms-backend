from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TransactionDraftCreate(BaseModel):
    """
    Foundation-only schema — no endpoint calls an AI model yet (see
    models/transaction_draft.py). This is here so the eventual voice/
    bulk-entry integration only needs to POST to the existing endpoint
    with this shape; nothing about the contract should need to change.
    """
    source: Literal["voice", "bulk_text"]
    raw_input: str


class TransactionDraftUpdate(BaseModel):
    """User corrections to a draft's parsed fields before confirming."""
    parsed_fields: dict


class TransactionDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    source: str
    raw_input: str
    parsed_fields: dict
    ambiguous_fields: dict
    status: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
