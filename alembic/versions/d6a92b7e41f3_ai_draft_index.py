"""Speed up per-user pending AI draft review lists.

Revision ID: d6a92b7e41f3
Revises: f6a0bc9111cd
"""
from alembic import op

revision = 'd6a92b7e41f3'
down_revision = 'f6a0bc9111cd'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_transaction_drafts_user_status_created', 'transaction_drafts', ['user_id', 'status', 'created_at'])


def downgrade():
    op.drop_index('ix_transaction_drafts_user_status_created', table_name='transaction_drafts')
