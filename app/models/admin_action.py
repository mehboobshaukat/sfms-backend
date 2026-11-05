from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminAction(Base):
    """
    Append-only record of sensitive admin actions (block, role changes, replies...).
    Metadata only - never message bodies, amounts or financial data. Nothing in the
    API updates or deletes these rows.
    """
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_label: Mapped[str] = mapped_column(String(160), nullable=False)  # username snapshot (or "cli")
    action: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
