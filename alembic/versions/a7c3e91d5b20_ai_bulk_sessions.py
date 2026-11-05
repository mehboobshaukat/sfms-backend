"""AI bulk import sessions (conversational clarification flow).

Revision ID: a7c3e91d5b20
Revises: d6a92b7e41f3
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c3e91d5b20'
down_revision = 'd6a92b7e41f3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ai_bulk_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slug', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False, server_default='bulk_text'),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('plan', sa.JSON(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='awaiting_answers'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_ai_bulk_sessions_id', 'ai_bulk_sessions', ['id'])
    op.create_index('ix_ai_bulk_sessions_slug', 'ai_bulk_sessions', ['slug'], unique=True)
    op.create_index('ix_ai_bulk_sessions_user_id', 'ai_bulk_sessions', ['user_id'])
    op.create_index('ix_ai_bulk_sessions_user_status', 'ai_bulk_sessions', ['user_id', 'status', 'created_at'])


def downgrade():
    op.drop_index('ix_ai_bulk_sessions_user_status', table_name='ai_bulk_sessions')
    op.drop_index('ix_ai_bulk_sessions_user_id', table_name='ai_bulk_sessions')
    op.drop_index('ix_ai_bulk_sessions_slug', table_name='ai_bulk_sessions')
    op.drop_index('ix_ai_bulk_sessions_id', table_name='ai_bulk_sessions')
    op.drop_table('ai_bulk_sessions')
