from datetime import datetime, timezone, timedelta

from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=7)


class TransactionDraft(Base, TimestampMixin, SlugMixin):
    """
    Staging area for AI-assisted transaction entry (voice / bulk paste).
    This is FOUNDATION ONLY — no code anywhere yet actually calls a
    speech-to-text or LLM service. The point of building this table now
    is so that whenever that integration is added, it only needs to
    populate rows here through the normal create/list/confirm/reject
    flow below; nothing about the schema or the rest of the app needs to
    change at that point.

    Design principle (do not violate when the AI integration is added):
    confirming a draft must always go through the normal, already-
    validated transaction_service.create_transaction() — never write
    directly into the transactions table from here. The AI proposes;
    the existing business-rule engine (loan-chain checks, budget
    tracking, account requirements, etc.) still has the final say.
    """
    __tablename__ = "transaction_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)  # voice | bulk_text
    raw_input: Mapped[str] = mapped_column(String, nullable=False)

    # Best-guess structured transaction fields, keyed the same as
    # TransactionCreate — but referencing related entities by SLUG
    # (e.g. {"category_slug": "cat-a8fe7a1bda05", ...}), never by id,
    # consistent with the rest of the system's public API contract.
    parsed_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Which of parsed_fields the model itself flagged as uncertain/
    # missing, and why — drives which drafts get surfaced for one-by-one
    # user confirmation vs. auto-accepted. e.g.
    # {"category_slug": "couldn't confidently match 'chai' to an existing category"}
    ambiguous_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="pending_review", nullable=False)  # pending_review|confirmed|rejected|expired
    resulting_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_default_expiry, nullable=False)

    resulting_transaction = relationship("Transaction")
