"""Country and optional address fields (currency derived by country, not stored).

Revision ID: 9f83a2b1c4d5
Revises: c926af381e02
"""
from alembic import op
import sqlalchemy as sa

revision = "9f83a2b1c4d5"
down_revision = "c926af381e02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve old users: unspecified country falls back to PKR for display.
    op.add_column("users", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("users", sa.Column("state", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("postal_code", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "postal_code")
    op.drop_column("users", "city")
    op.drop_column("users", "state")
    op.drop_column("users", "country")
