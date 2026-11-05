from datetime import datetime, timezone, timedelta

from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


class AiBulkSession(Base, TimestampMixin, SlugMixin):
    """One AI bulk-import conversation: the analysed plan + the user's answers.

    Holds NO financial postings. The AI reads the notes once; the user's answers
    are applied locally (no second AI call). When the user is satisfied the
    session is 'finalized' into reviewable TransactionDraft rows. Unfinished
    sessions expire after 24 hours.
    """
    __tablename__ = "ai_bulk_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="bulk_text")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # awaiting_answers | finalized | cancelled | expired
    status: Mapped[str] = mapped_column(String(20), default="awaiting_answers", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_default_expiry, nullable=False)
