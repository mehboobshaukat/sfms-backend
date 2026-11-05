"""merge conflicting heads

Revision ID: e447bfac1cae
Revises: 4d2f9dd05d8b, a64f2c31d9e7
Create Date: 2026-10-06 14:45:24.855058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e447bfac1cae'
down_revision: Union[str, None] = ('4d2f9dd05d8b', 'a64f2c31d9e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
