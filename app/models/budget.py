from datetime import date
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Budget(Base, TimestampMixin, SlugMixin):
    # No name-uniqueness constraint — a budget has no free-text name of
    # its own (it's defined by category + period), so there's nothing to
    # collide.
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # weekly|monthly|yearly
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    spent_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category = relationship("Category")

    @property
    def category_slug(self) -> str | None:
        return self.category.slug if self.category else None
