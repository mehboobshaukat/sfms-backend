from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class Account(Base, TimestampMixin, SlugMixin):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_account_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)  # cash|digital_wallet|bank|external
    account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # True only for "External"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user = relationship("User", back_populates="accounts")
    payment_methods = relationship("PaymentMethod", back_populates="account")
