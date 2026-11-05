"""merge conflicting heads

Revision ID: 4d2f9dd05d8b
Revises: 47a267ef44c3, d42c9e7b31f8
Create Date: 2026-10-06 14:44:14.761222

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d2f9dd05d8b'
down_revision: Union[str, None] = ('47a267ef44c3', 'd42c9e7b31f8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
