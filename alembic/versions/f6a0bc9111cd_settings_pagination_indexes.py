"""Composite indexes for tenant-scoped paginated lists.

Revision ID: f6a0bc9111cd
Revises: 9f83a2b1c4d5
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a0bc9111cd"
down_revision = "9f83a2b1c4d5"
branch_labels = None
depends_on = None

LOOKUP_TABLES = (
    "categories",
    "groups",
    "contacts",
    "transaction_types",
    "payment_methods",
    "priorities",
)


def upgrade():
    for table in LOOKUP_TABLES:
        op.create_index(
            f"ix_{table}_user_created_id",
            table,
            ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
            unique=False,
        )
    op.create_index(
        "ix_history_entries_user_created_id", "history_entries",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_history_entries_user_type_created_id", "history_entries",
        ["user_id", "entity_type", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_transactions_user_active_date_id", "transactions",
        ["user_id", "is_active", sa.text("txn_date DESC"), sa.text("id DESC")],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_transactions_user_active_date_id", table_name="transactions")
    op.drop_index("ix_history_entries_user_type_created_id", table_name="history_entries")
    op.drop_index("ix_history_entries_user_created_id", table_name="history_entries")
    for table in reversed(LOOKUP_TABLES):
        op.drop_index(f"ix_{table}_user_created_id", table_name=table)
