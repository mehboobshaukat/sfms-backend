"""
Auto-generated human-readable codes:
  Transaction -> TXN-{YYYYMMDD}-{4-digit daily sequence per user}
  Loan        -> LN-{YYYYMMDD}-{sequence per user}
"""
from datetime import date
from sqlalchemy import func, cast, Integer, select
from sqlalchemy.orm import Session


def generate_transaction_code(db: Session, user_id: int, txn_date: date) -> str:
    """Unique public transaction code, including after deletions.

    The transactions.transaction_code column is globally UNIQUE, not scoped
    by user. Counting a user's existing rows can reuse numbers after deletion
    and collide with other users. Allocate max existing daily suffix + 1.
    For PostgreSQL, one transaction-scoped advisory lock serializes concurrent
    allocations from BOTH normal and AI transaction creation paths.
    """
    from app.models.transaction import Transaction

    date_str = txn_date.strftime("%Y%m%d")
    prefix = f"TXN-{date_str}-"
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        # PostgreSQL transaction-scoped lock; released by commit/rollback.
        db.execute(select(func.pg_advisory_xact_lock(921064312))).scalar_one()

    last_number = db.query(func.max(cast(func.substr(Transaction.transaction_code, len(prefix) + 1), Integer))).filter(
        Transaction.transaction_code.like(f"{prefix}%")
    ).scalar()
    sequence = (last_number or 0) + 1
    return f"{prefix}{sequence:04d}"


def generate_loan_code(db: Session, user_id: int, loan_date: date) -> str:
    """Global unique loan code; shared by normal and AI-origin loan transactions."""
    from app.models.loan import Loan

    date_str = loan_date.strftime("%Y%m%d")
    prefix = f"LN-{date_str}-"
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(921064313))).scalar_one()
    last_number = db.query(func.max(cast(func.substr(Loan.loan_code, len(prefix) + 1), Integer))).filter(
        Loan.loan_code.like(f"{prefix}%")
    ).scalar()
    return f"{prefix}{(last_number or 0) + 1:04d}"
