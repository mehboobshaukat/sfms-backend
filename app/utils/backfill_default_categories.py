"""Idempotently backfill per-user defaults for existing users.

This helper originally covered categories only. It now also verifies the default
Cash account/payment method while preserving the same module entry point for
backward compatibility:

    python -m app.utils.backfill_default_categories

It is safe to re-run: existing user-created data is preserved and only missing
default rows/links are inserted.
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.crud.seed import seed_user_defaults
from app.models.user import User
from app.utils.seed_system_defaults import seed_system_defaults


def run() -> None:
    db = SessionLocal()
    try:
        seed_system_defaults(db)
        user_ids = db.execute(
            select(User.id).where(User.is_deleted == False)  # noqa: E712
        ).scalars().all()

        for user_id in user_ids:
            seed_user_defaults(db, user_id)

        db.commit()
        print(f"Default user data (categories/accounts/payment methods) verified/backfilled for {len(user_ids)} active user(s).")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
