"""Add auditable goal savings ledger without changing existing balances.

Revision ID: c926af381e02
Revises: 5f531a982034
"""
from alembic import op
import sqlalchemy as sa
revision = "c926af381e02"
down_revision = "5f531a982034"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("goal_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("goals.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_goal_allocation_request"))
    for column in ("user_id", "goal_id", "account_id"):
        op.create_index("ix_goal_allocations_"+column,"goal_allocations",[column])
    # Reuse preexisting Goal Saving default, if present.
    connection = op.get_bind()
    exists = connection.execute(sa.text("SELECT id FROM transaction_types WHERE user_id IS NULL AND name='Goal Saving'")).first()
    if not exists:
        from app.models.transaction_type import TransactionType
        from app.utils.slugs import generate_slug
        from sqlalchemy.orm import Session
        session = Session(bind=connection)
        session.add(TransactionType(user_id=None,name="Goal Saving",nature="transfer",is_default=True,
                                    slug=generate_slug(session,TransactionType,"transaction_type")))
        session.flush()

def downgrade():
    # Deliberately do not remove audit records or the seeded type automatically.
    raise RuntimeError("Manual review required: downgrade would destroy financial audit history")
