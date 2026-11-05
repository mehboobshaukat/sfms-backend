from datetime import date, time
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, Date, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class Transaction(Base, TimestampMixin):
    """
    NOTE on identifiers: Transaction does NOT use SlugMixin — its existing
    `transaction_code` (e.g. "TXN-00040") already is a unique,
    human-readable, immutable public identifier, so it plays the exact
    role a slug would. No second field needed.
    """
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    transaction_code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(100), nullable=True)

    txn_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    txn_time: Mapped[time] = mapped_column(Time, nullable=False)

    transaction_type_id: Mapped[int] = mapped_column(ForeignKey("transaction_types.id"), nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False, index=True)
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"), nullable=True, index=True)
    credit_purchase_id: Mapped[int | None] = mapped_column(ForeignKey("credit_purchases.id"), nullable=True, index=True)

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # Both nullable now (backend redesign): required-ness is enforced in
    # transaction_service by the type's nature, not by the DB —
    # inflow needs to_account only, outflow needs from_account only,
    # transfer needs both. See create_transaction()'s validation step.
    from_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    payment_method_id: Mapped[int] = mapped_column(ForeignKey("payment_methods.id"), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True, index=True)
    priority_id: Mapped[int] = mapped_column(ForeignKey("priorities.id"), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    charges: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    # "from_account" (default) | "to_account" — only meaningful when both
    # accounts are real (nature=transfer) and charges > 0:
    #   from_account: from -= amount+charges, to += amount   (sender pays extra on top)
    #   to_account:   from -= amount,          to += amount-charges (fee skimmed off what arrives)
    charges_borne_by: Mapped[str] = mapped_column(String(15), default="from_account", nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    goal_allocation = relationship("GoalAllocation", uselist=False, viewonly=True)
    loan = relationship("Loan", back_populates="transactions")
    credit_purchase = relationship("CreditPurchase", back_populates="transactions")
    transaction_type = relationship("TransactionType")
    category = relationship("Category")
    from_account = relationship("Account", foreign_keys=[from_account_id])
    to_account = relationship("Account", foreign_keys=[to_account_id])
    payment_method = relationship("PaymentMethod")
    contact = relationship("Contact")
    priority = relationship("Priority")

    # Page-local display labels: the list already eager-loads these relations.
    # Avoid six unbounded lookup API requests merely to render 20 rows.
    @property
    def transaction_type_label(self) -> str | None:
        return self.transaction_type.name if self.transaction_type else None

    @property
    def category_label(self) -> str | None:
        return self.category.name if self.category else None

    @property
    def from_account_label(self) -> str | None:
        return self.from_account.name if self.from_account else None

    @property
    def to_account_label(self) -> str | None:
        return self.to_account.name if self.to_account else None

    @property
    def contact_label(self) -> str | None:
        return self.contact.name if self.contact else None

    @property
    def priority_label(self) -> str | None:
        return self.priority.name if self.priority else None

    # Read-only slug accessors — TransactionOut reads these (via
    # from_attributes) instead of exposing the raw *_id FK columns,
    # consistent with the system-wide "never expose raw ids" policy.
    # Loan keeps its own human-readable loan_code rather than a slug.
    @property
    def goal_slug(self) -> str | None:
        return self.goal_allocation.goal.slug if self.goal_allocation else None

    @property
    def allocation_action(self) -> str | None:
        if not self.goal_allocation:
            return None
        return "release" if self.goal_allocation.amount < 0 else "allocate"

    @property
    def transaction_type_slug(self) -> str | None:
        return self.transaction_type.slug if self.transaction_type else None

    @property
    def category_slug(self) -> str | None:
        return self.category.slug if self.category else None

    @property
    def from_account_slug(self) -> str | None:
        return self.from_account.slug if self.from_account else None

    @property
    def to_account_slug(self) -> str | None:
        return self.to_account.slug if self.to_account else None

    @property
    def payment_method_slug(self) -> str | None:
        return self.payment_method.slug if self.payment_method else None

    @property
    def contact_slug(self) -> str | None:
        return self.contact.slug if self.contact else None

    @property
    def priority_slug(self) -> str | None:
        return self.priority.slug if self.priority else None

    @property
    def loan_code(self) -> str | None:
        return self.loan.loan_code if self.loan else None

    @property
    def credit_purchase_slug(self) -> str | None:
        return self.credit_purchase.slug if self.credit_purchase else None
