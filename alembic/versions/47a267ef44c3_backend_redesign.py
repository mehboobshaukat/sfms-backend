"""backend redesign: reusable category/group/contact, credit purchases,
transaction drafts, history/recycle-bin, slugs, charges_borne_by

Revision ID: 47a267ef44c3
Revises: 6f103cfdc490
Create Date: 2026-09-20 00:00:00.000000
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '47a267ef44c3'
down_revision: Union[str, None] = '6f103cfdc490'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that get a new `slug` column as part of this redesign, and the
# short prefix each one's generated slugs use (see app/utils/slugs.py —
# keep this list in sync with ENTITY_PREFIXES there).
SLUG_TABLES = [
    ("categories", "cat"),
    ("groups", "grp"),
    ("contacts", "con"),
    ("transaction_types", "tt"),
    ("priorities", "pr"),
    ("accounts", "acc"),
    ("payment_methods", "pm"),
    ("budgets", "bud"),
    ("goals", "gl"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. New tables -------------------------------------------------
    op.create_table(
        'credit_purchases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=32), nullable=False),
        sa.Column('credit_type', sa.String(length=10), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('item_description', sa.String(length=500), nullable=False),
        sa.Column('total_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('remaining_amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('purchase_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('is_completed', sa.Boolean(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credit_purchases_user_id', 'credit_purchases', ['user_id'])
    op.create_index('ix_credit_purchases_contact_id', 'credit_purchases', ['contact_id'])
    op.create_index('ix_credit_purchases_category_id', 'credit_purchases', ['category_id'])
    op.create_index('ix_credit_purchases_slug', 'credit_purchases', ['slug'], unique=True)

    op.create_table(
        'transaction_drafts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('raw_input', sa.String(), nullable=False),
        sa.Column('parsed_fields', sa.JSON(), nullable=False),
        sa.Column('ambiguous_fields', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('resulting_transaction_id', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['resulting_transaction_id'], ['transactions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_transaction_drafts_user_id', 'transaction_drafts', ['user_id'])
    op.create_index('ix_transaction_drafts_slug', 'transaction_drafts', ['slug'], unique=True)

    op.create_table(
        'history_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=32), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_slug', sa.String(length=32), nullable=False),
        sa.Column('entity_label', sa.String(length=200), nullable=False),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('performed_by', sa.String(length=10), nullable=False),
        sa.Column('before_snapshot', sa.JSON(), nullable=True),
        sa.Column('after_snapshot', sa.JSON(), nullable=True),
        sa.Column('is_undone', sa.Boolean(), nullable=False),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_history_entries_user_id', 'history_entries', ['user_id'])
    op.create_index('ix_history_entries_slug', 'history_entries', ['slug'], unique=True)
    op.create_index('ix_history_entries_entity_type', 'history_entries', ['entity_type'])
    op.create_index('ix_history_entries_entity_slug', 'history_entries', ['entity_slug'])

    op.create_table(
        'category_transaction_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('transaction_type_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id']),
        sa.ForeignKeyConstraint(['transaction_type_id'], ['transaction_types.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category_id', 'transaction_type_id', name='uq_category_transaction_type'),
    )
    op.create_index('ix_category_transaction_types_category_id', 'category_transaction_types', ['category_id'])
    op.create_index('ix_category_transaction_types_transaction_type_id', 'category_transaction_types', ['transaction_type_id'])

    op.create_table(
        'category_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id']),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category_id', 'group_id', name='uq_category_group'),
    )
    op.create_index('ix_category_groups_category_id', 'category_groups', ['category_id'])
    op.create_index('ix_category_groups_group_id', 'category_groups', ['group_id'])

    op.create_table(
        'group_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'contact_id', name='uq_group_contact'),
    )
    op.create_index('ix_group_contacts_group_id', 'group_contacts', ['group_id'])
    op.create_index('ix_group_contacts_contact_id', 'group_contacts', ['contact_id'])

    # ---- 2. Backfill join tables from the old ownership FKs, BEFORE dropping them ----
    op.execute(
        "INSERT INTO category_transaction_types (category_id, transaction_type_id, created_at) "
        "SELECT id, transaction_type_id, now() FROM categories WHERE transaction_type_id IS NOT NULL"
    )
    op.execute(
        "INSERT INTO category_groups (category_id, group_id, created_at) "
        "SELECT category_id, id, now() FROM groups WHERE category_id IS NOT NULL"
    )
    op.execute(
        "INSERT INTO group_contacts (group_id, contact_id, created_at) "
        "SELECT group_id, id, now() FROM contacts WHERE group_id IS NOT NULL"
    )

    # ---- 3. Drop the old single-parent columns + their constraints/indexes ----
    op.drop_constraint('uq_category_user_type_name', 'categories', type_='unique')
    op.drop_index('ix_categories_transaction_type_id', table_name='categories')
    op.drop_column('categories', 'transaction_type_id')

    op.drop_index('ix_groups_category_id', table_name='groups')
    op.drop_column('groups', 'category_id')

    op.drop_index('ix_contacts_group_id', table_name='contacts')
    op.drop_column('contacts', 'group_id')

    # ---- 4. Add `slug` (nullable first), backfill, then lock it down ----
    for table, _prefix in SLUG_TABLES:
        op.add_column(table, sa.Column('slug', sa.String(length=32), nullable=True))

    for table, prefix in SLUG_TABLES:
        rows = bind.execute(sa.text(f"SELECT id FROM {table} WHERE slug IS NULL")).fetchall()
        for (row_id,) in rows:
            # Collision is practically impossible at 12 hex chars, but loop
            # defensively rather than assume.
            for _ in range(5):
                candidate = f"{prefix}-{secrets.token_hex(6)}"
                clash = bind.execute(
                    sa.text(f"SELECT 1 FROM {table} WHERE slug = :s"), {"s": candidate}
                ).scalar_one_or_none()
                if clash is None:
                    break
            bind.execute(sa.text(f"UPDATE {table} SET slug = :s WHERE id = :i"), {"s": candidate, "i": row_id})

    for table, _prefix in SLUG_TABLES:
        op.alter_column(table, 'slug', nullable=False)
        op.create_index(f'ix_{table}_slug', table, ['slug'], unique=True)

    # ---- 5. Per-user name-uniqueness constraints on the lookup entities ----
    op.create_unique_constraint('uq_category_user_name', 'categories', ['user_id', 'name'])
    op.create_unique_constraint('uq_group_user_name', 'groups', ['user_id', 'name'])
    op.create_unique_constraint('uq_contact_user_name', 'contacts', ['user_id', 'name'])
    op.create_unique_constraint('uq_transaction_type_user_name', 'transaction_types', ['user_id', 'name'])
    op.create_unique_constraint('uq_priority_user_name', 'priorities', ['user_id', 'name'])
    op.create_unique_constraint('uq_account_user_name', 'accounts', ['user_id', 'name'])
    op.create_unique_constraint('uq_payment_method_user_name', 'payment_methods', ['user_id', 'name'])

    # ---- 6. Transaction table changes ----
    op.add_column('transactions', sa.Column('charges_borne_by', sa.String(length=15), nullable=False, server_default='from_account'))
    op.alter_column('transactions', 'charges_borne_by', server_default=None)
    op.add_column('transactions', sa.Column('credit_purchase_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_transactions_credit_purchase_id', 'transactions', 'credit_purchases', ['credit_purchase_id'], ['id'])
    op.create_index('ix_transactions_credit_purchase_id', 'transactions', ['credit_purchase_id'])
    # Nature-based account requirements (see transaction_service.py):
    # inflow only needs to_account, outflow only needs from_account.
    op.alter_column('transactions', 'from_account_id', nullable=True)
    op.alter_column('transactions', 'to_account_id', nullable=True)


def downgrade() -> None:
    op.alter_column('transactions', 'to_account_id', nullable=False)
    op.alter_column('transactions', 'from_account_id', nullable=False)
    op.drop_index('ix_transactions_credit_purchase_id', table_name='transactions')
    op.drop_constraint('fk_transactions_credit_purchase_id', 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'credit_purchase_id')
    op.drop_column('transactions', 'charges_borne_by')

    op.drop_constraint('uq_payment_method_user_name', 'payment_methods', type_='unique')
    op.drop_constraint('uq_account_user_name', 'accounts', type_='unique')
    op.drop_constraint('uq_priority_user_name', 'priorities', type_='unique')
    op.drop_constraint('uq_transaction_type_user_name', 'transaction_types', type_='unique')
    op.drop_constraint('uq_contact_user_name', 'contacts', type_='unique')
    op.drop_constraint('uq_group_user_name', 'groups', type_='unique')
    op.drop_constraint('uq_category_user_name', 'categories', type_='unique')

    for table, _prefix in SLUG_TABLES:
        op.drop_index(f'ix_{table}_slug', table_name=table)
        op.drop_column(table, 'slug')

    op.add_column('contacts', sa.Column('group_id', sa.Integer(), nullable=True))
    op.add_column('groups', sa.Column('category_id', sa.Integer(), nullable=True))
    op.add_column('categories', sa.Column('transaction_type_id', sa.Integer(), nullable=True))
    # NOTE: this downgrade does not attempt to restore data into these
    # columns from the join tables — it only restores the schema shape.
    # Going backward across this migration is a last resort, not a
    # data-safe operation.
    op.create_index('ix_contacts_group_id', 'contacts', ['group_id'])
    op.create_index('ix_groups_category_id', 'groups', ['category_id'])
    op.create_index('ix_categories_transaction_type_id', 'categories', ['transaction_type_id'])
    op.create_unique_constraint('uq_category_user_type_name', 'categories', ['user_id', 'transaction_type_id', 'name'])

    op.drop_table('group_contacts')
    op.drop_table('category_groups')
    op.drop_table('category_transaction_types')
    op.drop_table('history_entries')
    op.drop_table('transaction_drafts')
    op.drop_table('credit_purchases')
