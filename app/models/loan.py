from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class Loan(Base, TimestampMixin):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    loan_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    loan_type: Mapped[str] = mapped_column(String(10), nullable=False)  # received | given
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)

    principal_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact")
    transactions = relationship("Transaction", back_populates="loan")

    @property
    def contact_slug(self) -> str | None:
        return self.contact.slug if self.contact else None
