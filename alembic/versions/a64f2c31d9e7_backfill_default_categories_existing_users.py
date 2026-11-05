"""backfill default categories for existing users

Revision ID: a64f2c31d9e7
Revises: d42c9e7b31f8
Create Date: 2026-10-06 14:35:00.000000

This is a data-only migration. No table, relationship, API route, or existing
system structure is changed. New registrations are already seeded by
app.crud.seed.seed_user_defaults; this migration only gives accounts that
existed before that feature the same category baseline.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.seed import seed_user_categories
from app.models.user import User
from app.utils.seed_system_defaults import seed_system_defaults


revision: str = "a64f2c31d9e7"
down_revision: Union[str, None] = "d42c9e7b31f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    db = Session(bind=op.get_bind(), autoflush=False, expire_on_commit=False)

    # Make this robust even if the app has not yet been started after a fresh
    # deployment, because category links depend on the 15 system type rows.
    seed_system_defaults(db)

    user_ids = db.execute(
        select(User.id).where(User.is_deleted == False)  # noqa: E712
    ).scalars().all()
    for user_id in user_ids:
        seed_user_categories(db, user_id)

    db.flush()


def downgrade() -> None:
    # Intentionally non-destructive: categories may have been edited or used in
    # transactions after the upgrade. Removing them on downgrade would risk
    # deleting user data. This migration changes data only, not schema.
    pass
