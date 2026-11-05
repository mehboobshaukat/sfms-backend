"""Explicitly reviewed, multilingual text-to-transaction extraction contract."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class BulkAnswers(BaseModel):
    # Explicit batch defaults (the legacy one-shot /parse API). None means the
    # user did not provide that default. Always validated against the logged-in
    # user's own lookup slugs. The conversational flow uses AnswerItem instead.
    txn_date: date | None = None
    txn_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    account_slug: str | None = None
    payment_method_slug: str | None = None
    priority_slug: str | None = None
    transaction_type_slug: str | None = None
    category_slug: str | None = None
    contact_slug: str | None = None


class BulkParseRequest(BaseModel):
    raw_text: str = Field(min_length=3, max_length=50000)
    # In the future the same pipeline can accept a separately transcribed voice input.
    source: str = Field(default="bulk_text", pattern=r"^(bulk_text|voice)$")
    answers: BulkAnswers = Field(default_factory=BulkAnswers)


class Candidate(BaseModel):
    source_line: int
    description: str
    amount: str | None
    amount_note: str | None
    transaction_type_name: str | None
    category_name: str | None
    contact_name: str | None
    account_name: str | None
    payment_method_name: str | None
    priority_name: str | None
    txn_date: str | None
    warnings: list[str]
    # AI suggestions, not authoritative: the backend computes actual missing
    # fields independently for each item and then builds grouped questions.
    missing_fields: list[str] = Field(default_factory=list)
    # Added for the conversational flow. Defaults keep older mocks/clients valid;
    # providers.py strips the "default" keys from the provider-facing schema.
    # direction: from the entry's WORDING only ('unknown' when it has no cue).
    direction: str | None = None  # outflow | inflow | transfer | unknown
    # True for an amount that sits inside another entry's narrative
    # (e.g. the 80 in '50(milk, baji gave 80 ...)'), not the entry's main amount.
    is_inner: bool = False
    # Structured optional fields. Null indicates absent, not fabricated.
    txn_time: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    loan_code: str | None = None
    credit_purchase_slug: str | None = None
    charges: str | None = None
    charges_borne_by: str | None = None
    reference_no: str | None = None
    remarks: str | None = None
    due_date: str | None = None


class CandidateBatch(BaseModel):
    transactions: list[Candidate]


class ContextOut(BaseModel):
    """Batch-wide details the user stated in non-transaction text (LLM output)."""
    txn_date: str | None            # ISO date, only when day, month AND year are explicit
    txn_date_day_month: str | None  # 'DD-MM' when the year is missing
    is_today: bool                  # the user said the entries are from today (aaj/today)
    txn_time: str | None            # 'HH:MM' 24h
    account_name: str | None
    payment_method_name: str | None
    priority_name: str | None
    transaction_type_name: str | None
    category_name: str | None
    notes: list[str]


class BulkPreflightRequest(BaseModel):
    raw_text: str = Field(min_length=3, max_length=50000)


# ---- Conversational flow (analyze -> answer -> finalize) -------------------

class AnalyzeRequest(BaseModel):
    raw_text: str = Field(min_length=3, max_length=50000)
    source: str = Field(default="bulk_text", pattern=r"^(bulk_text|voice)$")


class AnswerItem(BaseModel):
    """One answer to one question.

    mode='set'  -> value applies to every draft item in item_ids
    mode='skip' -> user will fill this in each draft; do not ask again
    mode='each' -> user wants to be asked per entry instead of once for the group
    """
    field: str = Field(min_length=1, max_length=40)
    item_ids: list[int] = Field(default_factory=list, max_length=400)
    value: str | None = Field(default=None, max_length=500)
    mode: Literal["set", "skip", "each"] = "set"


class AnswersRequest(BaseModel):
    answers: list[AnswerItem] = Field(default_factory=list, max_length=200)


# The LLM may improve question wording, but must NEVER return HTML or choose
# the field/option values that are rendered to the user.
class SuggestedQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=3, max_length=260)
    hint: str | None = Field(default=None, max_length=220)


class SuggestedQuestionBatch(BaseModel):
    questions: list[SuggestedQuestion] = Field(default_factory=list, max_length=20)
