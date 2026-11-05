from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Category(Base, TimestampMixin, SlugMixin):
    """
    Standalone, reusable per-user entity (backend redesign: Option B).
    NOT owned by a single TransactionType anymore — which transaction
    types it can be used under is tracked separately via the
    CategoryTransactionType link table, so the same category (e.g.
    "Family") can be linked under both "Support Given" and
    "Support Received" instead of needing a duplicate row per type.
    """
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_category_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    transaction_type_links = relationship("CategoryTransactionType", back_populates="category", cascade="all, delete-orphan")
    group_links = relationship("CategoryGroup", back_populates="category", cascade="all, delete-orphan")

    @property
    def transaction_type_labels(self) -> dict[str, str]:
        return {link.transaction_type.slug: link.transaction_type.name for link in self.transaction_type_links}

    @property
    def transaction_type_slugs(self) -> list[str]:
        return [link.transaction_type.slug for link in self.transaction_type_links]

    @property
    def group_slugs(self) -> list[str]:
        return [link.group.slug for link in self.group_links]
