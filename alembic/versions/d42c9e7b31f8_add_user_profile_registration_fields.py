"""add user profile and registration fields

Revision ID: d42c9e7b31f8
Revises: b1876cf4a2bb
Create Date: 2026-10-05 14:50:00.000000

This migration is intentionally limited to the registration/profile fields
requested in the October 2026 update. No existing table or relationship is
renamed or restructured.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d42c9e7b31f8"
down_revision: Union[str, None] = "b1876cf4a2bb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("phone_number", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("profile_image_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "profile_image_url")
    op.drop_column("users", "phone_number")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
