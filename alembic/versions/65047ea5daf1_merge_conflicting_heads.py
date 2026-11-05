"""merge conflicting heads

Revision ID: 65047ea5daf1
Revises: e447bfac1cae
Create Date: 2026-10-06 14:47:14.486107

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65047ea5daf1'
down_revision: Union[str, None] = 'e447bfac1cae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
