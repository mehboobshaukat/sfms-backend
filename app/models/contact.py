from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Contact(Base, TimestampMixin, SlugMixin):
    """
    Standalone, reusable per-user entity (Option B) — not owned by a
    single Group. Which groups it belongs to is tracked via
    GroupContact, so the same real person ("Ahmed") only ever exists as
    one row, linked into as many groups as needed. Name uniqueness is
    enforced per user (case/whitespace-insensitive) specifically so two
    different real people don't collapse into one record by accident —
    e.g. "Ahmed" the cousin and "Ahmed" the friend must be entered as
    distinguishable names.
    """
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_contact_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    group_links = relationship("GroupContact", back_populates="contact", cascade="all, delete-orphan")

    @property
    def group_labels(self) -> dict[str, str]:
        return {link.group.slug: link.group.name for link in self.group_links}

    @property
    def group_slugs(self) -> list[str]:
        return [link.group.slug for link in self.group_links]
