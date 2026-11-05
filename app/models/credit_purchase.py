from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, Date, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class CreditPurchase(Base, TimestampMixin, SlugMixin):
    """
    Goods/items received or given on credit — deliberately NOT part of
    the Loan system (Loan is cash-only). Creating a CreditPurchase row
    has ZERO effect on account balances: no money has actually moved yet,
    only a liability (credit_type="received") or receivable
    (credit_type="given") has been recorded — visible immediately via
    remaining_amount, before any payment is made. Money only actually
    moves when a settlement Transaction is created against it (nature
    outflow "Item Payment Made" for received, inflow "Item Payment
    Received" for given), linked via Transaction.credit_purchase_id.
    Full or installment payoff both work the same way: repeated partial
    settlement transactions reduce remaining_amount until it hits 0.
    """
    __tablename__ = "credit_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    credit_type: Mapped[str] = mapped_column(String(10), nullable=False)  # received | given
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True, index=True)

    item_description: Mapped[str] = mapped_column(String(500), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact")
    category = relationship("Category")
    transactions = relationship("Transaction", back_populates="credit_purchase")

    @property
    def contact_slug(self) -> str | None:
        return self.contact.slug if self.contact else None

    @property
    def category_slug(self) -> str | None:
        return self.category.slug if self.category else None
