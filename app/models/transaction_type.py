from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, SlugMixin


class TransactionType(Base, TimestampMixin, SlugMixin):
    __tablename__ = "transaction_types"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_transaction_type_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)  # NULL = system default
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # inflow | outflow | transfer — "transfer" = plain move of money between
    # two of the user's own accounts (e.g. the "Transfer" / "Withdraw" /
    # "Deposit" system defaults), net-worth neutral, excluded from
    # income/expense analytics.
    nature: Mapped[str] = mapped_column(String(10), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    category_links = relationship("CategoryTransactionType", back_populates="transaction_type", cascade="all, delete-orphan")
