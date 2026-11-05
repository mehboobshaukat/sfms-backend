"""Append-only ledger: signed reservations. No change to physical account balances."""
from decimal import Decimal
from sqlalchemy import Integer, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class GoalAllocation(Base):
    __tablename__ = "goal_allocations"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_goal_allocation_request"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2), nullable=False)  # positive allocation, negative release
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    transaction = relationship("Transaction")
    goal = relationship("Goal")
