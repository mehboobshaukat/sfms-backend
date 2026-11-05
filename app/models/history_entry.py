from datetime import datetime, timezone, timedelta

from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=30)


class HistoryEntry(Base, TimestampMixin, SlugMixin):
    """
    User-facing activity history — this is the recycle bin AND the
    "undo my last change" mechanism in one: it's the same log, just
    filtered differently in the UI (action="delete" + not yet purged
    == recycle bin view; any entry == undo candidate). Every create/
    update/delete on any entity_type gets one row here, from either the
    user or (once wired up) the AI drafting flow.

    Retention: entries older than `expires_at` (30 days from creation,
    matching the user's chosen retention window — see
    app/utils/purge_deleted_users.py-style cron pattern) are purged
    automatically. The user can also delete an entry manually at any
    time before that — doing so just forfeits the ability to undo/
    restore that particular change, it never touches the live entity.

    Undo mechanics:
      - undo a "create"  -> delete the entity (created in error)
      - undo an "update" -> re-apply before_snapshot
      - undo a "delete"  -> restore from before_snapshot
    For entities with real financial side effects (Transaction,
    CreditPurchase settlements, Loan settlements), undo re-runs the same
    apply/reverse logic used by normal create/delete — it is NOT a raw
    row overwrite, since other transactions may have happened in the
    meantime and balances must stay correct in relative terms.
    """
    __tablename__ = "history_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    entity_slug: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_label: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. "Grocery - Rs 2,000" — readable even after purge

    action: Mapped[str] = mapped_column(String(10), nullable=False)  # create | update | delete
    performed_by: Mapped[str] = mapped_column(String(10), default="user", nullable=False)  # user | ai | system

    before_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    is_undone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_default_expiry, nullable=False)
