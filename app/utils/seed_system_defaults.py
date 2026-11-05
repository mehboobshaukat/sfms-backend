"""
Seeds system-wide default rows (user_id = NULL):
  - 15 default TransactionTypes
  - 3 default Priorities (Emergency, Necessary, Wish)

Run this ONCE after your first migration:
    python -m app.utils.seed_system_defaults

Safe to re-run — it checks for existing rows before inserting, and also
backfills anything that's missing without touching rows that already exist.
"""
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.transaction_type import TransactionType
from app.models.priority import Priority
from app.utils.slugs import generate_slug

# 11 original defaults (Income..Other) + Withdraw/Deposit (backend
# redesign: dedicated transfer-nature types for the common Bank/Wallet
# <-> Cash movement, clearer than making everyone use generic "Transfer")
# + the 2 settlement types the CreditPurchase feature needs to exist
# (Item Payment Made/Received) = 15 total.
DEFAULT_TRANSACTION_TYPES = [
    ("Income", "inflow"),
    ("Expense", "outflow"),
    ("Transfer", "transfer"),
    ("Withdraw", "transfer"),  # Bank/Wallet -> Cash
    ("Deposit", "transfer"),  # Cash -> Bank/Wallet
    ("Give Support", "outflow"),
    ("Take Support", "inflow"),
    ("Loan Received", "inflow"),
    ("Loan Repaid", "outflow"),
    ("Loan Given", "outflow"),
    ("Loan Collected", "inflow"),
    ("Charges", "outflow"),
    ("Refund", "inflow"),
    ("Item Payment Made", "outflow"),  # settles a "received" CreditPurchase
    ("Item Payment Received", "inflow"),  # settles a "given" CreditPurchase
    ("Other", "outflow"),  # user must confirm/change nature when first used
]

DEFAULT_PRIORITIES = ["Emergency", "Necessary", "Wish"]


def seed_system_defaults(db: Session) -> tuple[int, int]:
    """Seed missing system defaults into an existing SQLAlchemy session.

    Returning counts keeps the existing command useful while also allowing a
    data-only Alembic migration to seed transaction types on the same database
    connection before backfilling categories for pre-existing users.
    """
    existing_types = {
        t.name for t in db.query(TransactionType).filter(TransactionType.user_id.is_(None)).all()
    }
    added_types = 0
    for name, nature in DEFAULT_TRANSACTION_TYPES:
        if name not in existing_types:
            db.add(TransactionType(
                user_id=None,
                name=name,
                nature=nature,
                is_default=True,
                slug=generate_slug(db, TransactionType, "transaction_type"),
            ))
            db.flush()  # the next slug collision check should see this row
            existing_types.add(name)
            added_types += 1

    existing_priorities = {
        p.name for p in db.query(Priority).filter(Priority.user_id.is_(None)).all()
    }
    added_priorities = 0
    for name in DEFAULT_PRIORITIES:
        if name not in existing_priorities:
            db.add(Priority(
                user_id=None,
                name=name,
                is_default=True,
                slug=generate_slug(db, Priority, "priority"),
            ))
            db.flush()
            existing_priorities.add(name)
            added_priorities += 1

    return added_types, added_priorities


def run():
    db = SessionLocal()
    try:
        added_types, added_priorities = seed_system_defaults(db)
        db.commit()
        print(
            f"System defaults OK — {len(DEFAULT_TRANSACTION_TYPES)} TransactionTypes "
            f"({added_types} newly added), {len(DEFAULT_PRIORITIES)} Priorities "
            f"({added_priorities} newly added)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    run()
