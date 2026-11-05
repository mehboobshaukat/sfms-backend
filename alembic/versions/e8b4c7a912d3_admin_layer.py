"""Admin layer: contact messages, replies, admin audit trail, block details, single super admin.

Revision ID: e8b4c7a912d3
Revises: a7c3e91d5b20
"""
from alembic import op
import sqlalchemy as sa

revision = "e8b4c7a912d3"
down_revision = "a7c3e91d5b20"
branch_labels = None
depends_on = None


def upgrade():
    # --- users: block details + at most ONE super admin (enforced by the database) ---
    op.add_column("users", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("blocked_reason", sa.String(300), nullable=True))
    op.create_index(
        "uq_users_single_super_admin", "users", ["role"], unique=True,
        postgresql_where=sa.text("role = 'super_admin'"),
        sqlite_where=sa.text("role = 'super_admin'"),
    )

    # --- contact form inbox ---
    op.create_table(
        "contact_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contact_messages_id", "contact_messages", ["id"])
    op.create_index("ix_contact_messages_slug", "contact_messages", ["slug"], unique=True)
    op.create_index("ix_contact_messages_email", "contact_messages", ["email"])
    op.create_index("ix_contact_messages_ip_hash", "contact_messages", ["ip_hash"])
    op.create_index("ix_contact_messages_inbox", "contact_messages", ["deleted_at", "is_archived", "created_at"])
    op.create_index("ix_contact_messages_status", "contact_messages", ["status"])

    op.create_table(
        "contact_message_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("contact_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("admin_name", sa.String(160), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contact_message_replies_id", "contact_message_replies", ["id"])
    op.create_index("ix_contact_message_replies_message_id", "contact_message_replies", ["message_id"])

    # --- append-only admin audit trail (metadata only) ---
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_label", sa.String(160), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_ref", sa.String(100), nullable=False),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_actions_id", "admin_actions", ["id"])
    op.create_index("ix_admin_actions_actor_id", "admin_actions", ["actor_id"])
    op.create_index("ix_admin_actions_action", "admin_actions", ["action"])
    op.create_index("ix_admin_actions_created_at", "admin_actions", ["created_at"])


def downgrade():
    op.drop_table("admin_actions")
    op.drop_table("contact_message_replies")
    op.drop_table("contact_messages")
    op.drop_index("uq_users_single_super_admin", table_name="users")
    op.drop_column("users", "blocked_reason")
    op.drop_column("users", "blocked_at")
