from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import SlugMixin, TimestampMixin


class ContactMessage(Base, TimestampMixin, SlugMixin):
    """
    A submission from the public contact form. NOT the same thing as
    app.models.contact.Contact (which is a person a user lends to / borrows from).

    `message` is attacker-controlled free text: it must only ever be rendered as
    plain text by clients, never as HTML.

    Delete is a soft delete (`deleted_at`) -> "Trash". Trashed messages are
    purged for good after 30 days (see contact_message_service.purge_trashed),
    the same retention window History uses.
    """
    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)  # new | in_progress | resolved
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # HMAC of the submitter's IP (never the raw IP) - only used for abuse limits.
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    replies = relationship(
        "ContactMessageReply", back_populates="message",
        cascade="all, delete-orphan", order_by="ContactMessageReply.id",
    )

    __table_args__ = (
        Index("ix_contact_messages_inbox", "deleted_at", "is_archived", "created_at"),
        Index("ix_contact_messages_status", "status"),
    )


class ContactMessageReply(Base, TimestampMixin):
    """An email reply an admin sent from the panel. Only stored after delivery succeeded."""
    __tablename__ = "contact_message_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("contact_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    admin_name: Mapped[str] = mapped_column(String(160), nullable=False)  # snapshot, survives admin removal
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    message = relationship("ContactMessage", back_populates="replies")
