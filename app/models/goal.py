from datetime import date
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Goal(Base, TimestampMixin, SlugMixin):
    # Deliberately excluded from name-uniqueness — re-using a goal name
    # like "Emergency Fund" across different years/cycles is normal.
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    saved_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority_id: Mapped[int | None] = mapped_column(ForeignKey("priorities.id"), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    priority = relationship("Priority")

    @property
    def priority_slug(self) -> str | None:
        return self.priority.slug if self.priority else None
