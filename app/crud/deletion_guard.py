"""
Hard-delete dependency guard.

Backend redesign: Category/Group/Contact are now standalone, reusable
entities (Option B) linked via join tables (see models/links.py), so
"delete" here always means "permanently remove everywhere" — the
counterpart lightweight action is "unlink" (remove one association,
implemented directly in app/crud/lookups.py's link_*/unlink_* helpers,
no guard needed since it's non-destructive and reversible).

Permanently deleting one of these entities cascades to anything that
would become ORPHANED as a result (a Group with no remaining Category
links, a Contact with no remaining Group links) — not to everything it
was ever linked to, since other links may still be legitimately in use.

check_*() -> DeletionPlan; delete_*() performs the real cascade once the
caller has confirmed. Same 409-then-confirm contract as before.
"""
from dataclasses import dataclass, field

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.transaction_type import TransactionType
from app.models.category import Category
from app.models.group import Group
from app.models.contact import Contact
from app.models.links import CategoryTransactionType, CategoryGroup, GroupContact
from app.models.account import Account
from app.models.payment_method import PaymentMethod
from app.models.priority import Priority
from app.models.budget import Budget
from app.models.goal import Goal
from app.models.loan import Loan
from app.models.credit_purchase import CreditPurchase
from app.models.transaction import Transaction


@dataclass
class DeletionPlan:
    blocked: bool = False
    reason: str | None = None
    cascade: dict = field(default_factory=dict)

    @property
    def needs_confirmation(self) -> bool:
        return any(v > 0 for v in self.cascade.values())

    def as_detail(self) -> dict:
        if self.blocked:
            return {"status": "blocked", "message": self.reason}
        return {"status": "confirm", "message": self._cascade_message(), "cascade": self.cascade}

    def _cascade_message(self) -> str:
        parts = [f"{count} {label}" for label, count in self.cascade.items() if count > 0]
        return "Deleting this will also permanently delete " + ", ".join(parts) + ". This cannot be undone."


def _active_txn_count(db: Session, **filters) -> int:
    stmt = select(func.count(Transaction.id)).where(Transaction.is_active == True)  # noqa: E712
    for key, value in filters.items():
        stmt = stmt.where(getattr(Transaction, key) == value)
    return db.execute(stmt).scalar_one()


# --------------------------------------------------------------------- #
# Contact (leaf — nothing links FROM anything else TO a contact except
# GroupContact, loans, and transactions)
# --------------------------------------------------------------------- #
def check_contact_deletion(db: Session, user_id: int, contact: Contact) -> DeletionPlan:
    loan_count = db.execute(select(func.count(Loan.id)).where(Loan.contact_id == contact.id)).scalar_one()
    cp_count = db.execute(select(func.count(CreditPurchase.id)).where(CreditPurchase.contact_id == contact.id)).scalar_one()
    txn_count = _active_txn_count(db, contact_id=contact.id, user_id=user_id)
    if loan_count or cp_count or txn_count:
        parts = []
        if loan_count:
            parts.append(f"{loan_count} loan(s)")
        if cp_count:
            parts.append(f"{cp_count} credit purchase(s)")
        if txn_count:
            parts.append(f"{txn_count} transaction(s)")
        return DeletionPlan(blocked=True, reason=f"Cannot delete contact '{contact.name}': referenced by {' and '.join(parts)}. Remove those first.")
    group_link_count = db.execute(select(func.count(GroupContact.id)).where(GroupContact.contact_id == contact.id)).scalar_one()
    return DeletionPlan(cascade={"group links": group_link_count})


def delete_contact(db: Session, contact: Contact) -> None:
    db.query(GroupContact).filter(GroupContact.contact_id == contact.id).delete(synchronize_session=False)
    db.delete(contact)


# --------------------------------------------------------------------- #
# Group — cascades to orphaned contacts (contacts with NO remaining
# group link once this group's links are removed)
# --------------------------------------------------------------------- #
def check_group_deletion(db: Session, user_id: int, group: Group) -> DeletionPlan:
    contact_ids = [
        row[0] for row in db.execute(select(GroupContact.contact_id).where(GroupContact.group_id == group.id)).all()
    ]
    orphaned = 0
    for cid in contact_ids:
        other_links = db.execute(
            select(func.count(GroupContact.id)).where(GroupContact.contact_id == cid, GroupContact.group_id != group.id)
        ).scalar_one()
        if other_links == 0:
            contact = db.execute(select(Contact).where(Contact.id == cid)).scalar_one()
            sub = check_contact_deletion(db, user_id, contact)
            if sub.blocked:
                return DeletionPlan(blocked=True, reason=f"Cannot delete group '{group.name}': its contact '{contact.name}' is still in use ({sub.reason.split(': ', 1)[-1]})")
            orphaned += 1
    category_link_count = db.execute(select(func.count(CategoryGroup.id)).where(CategoryGroup.group_id == group.id)).scalar_one()
    return DeletionPlan(cascade={"category links": category_link_count, "orphaned contacts": orphaned})


def delete_group(db: Session, user_id: int, group: Group) -> None:
    contact_ids = [
        row[0] for row in db.execute(select(GroupContact.contact_id).where(GroupContact.group_id == group.id)).all()
    ]
    db.query(CategoryGroup).filter(CategoryGroup.group_id == group.id).delete(synchronize_session=False)
    db.query(GroupContact).filter(GroupContact.group_id == group.id).delete(synchronize_session=False)
    db.flush()
    for cid in contact_ids:
        other_links = db.execute(select(func.count(GroupContact.id)).where(GroupContact.contact_id == cid)).scalar_one()
        if other_links == 0:
            contact = db.execute(select(Contact).where(Contact.id == cid)).scalar_one()
            db.delete(contact)
    db.flush()
    db.delete(group)


# --------------------------------------------------------------------- #
# Category — cascades to orphaned groups (and, transitively, their
# orphaned contacts), plus its own budgets
# --------------------------------------------------------------------- #
def check_category_deletion(db: Session, user_id: int, category: Category) -> DeletionPlan:
    txn_count = _active_txn_count(db, category_id=category.id, user_id=user_id)
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete category '{category.name}': used by {txn_count} transaction(s). Delete or reassign those transactions first.")

    group_ids = [row[0] for row in db.execute(select(CategoryGroup.group_id).where(CategoryGroup.category_id == category.id)).all()]
    orphaned_groups = 0
    orphaned_contacts_total = 0
    for gid in group_ids:
        other_links = db.execute(
            select(func.count(CategoryGroup.id)).where(CategoryGroup.group_id == gid, CategoryGroup.category_id != category.id)
        ).scalar_one()
        if other_links == 0:
            group = db.execute(select(Group).where(Group.id == gid)).scalar_one()
            sub = check_group_deletion(db, user_id, group)
            if sub.blocked:
                return DeletionPlan(blocked=True, reason=f"Cannot delete category '{category.name}': {sub.reason}")
            orphaned_groups += 1
            orphaned_contacts_total += sub.cascade.get("orphaned contacts", 0)

    budget_count = db.execute(select(func.count(Budget.id)).where(Budget.category_id == category.id, Budget.user_id == user_id)).scalar_one()
    type_link_count = db.execute(select(func.count(CategoryTransactionType.id)).where(CategoryTransactionType.category_id == category.id)).scalar_one()
    return DeletionPlan(cascade={"transaction type links": type_link_count, "orphaned groups": orphaned_groups, "orphaned contacts": orphaned_contacts_total, "budgets": budget_count})


def delete_category(db: Session, user_id: int, category: Category) -> None:
    group_ids = [row[0] for row in db.execute(select(CategoryGroup.group_id).where(CategoryGroup.category_id == category.id)).all()]
    db.query(CategoryTransactionType).filter(CategoryTransactionType.category_id == category.id).delete(synchronize_session=False)
    db.query(CategoryGroup).filter(CategoryGroup.category_id == category.id).delete(synchronize_session=False)
    db.flush()
    for gid in group_ids:
        other_links = db.execute(select(func.count(CategoryGroup.id)).where(CategoryGroup.group_id == gid)).scalar_one()
        if other_links == 0:
            group = db.execute(select(Group).where(Group.id == gid)).scalar_one()
            delete_group(db, user_id, group)
    budgets = list(db.execute(select(Budget).where(Budget.category_id == category.id, Budget.user_id == user_id)).scalars().all())
    for b in budgets:
        db.delete(b)
    db.flush()
    db.delete(category)


# --------------------------------------------------------------------- #
# TransactionType — cascades to orphaned categories (and transitively
# their orphaned groups/contacts/budgets)
# --------------------------------------------------------------------- #
def check_transaction_type_deletion(db: Session, user_id: int, txn_type: TransactionType) -> DeletionPlan:
    if txn_type.user_id is None:
        return DeletionPlan(blocked=True, reason="System default transaction types cannot be deleted.")
    txn_count = _active_txn_count(db, transaction_type_id=txn_type.id, user_id=user_id)
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete transaction type '{txn_type.name}': used by {txn_count} transaction(s). Delete or reassign those transactions first.")

    category_ids = [row[0] for row in db.execute(select(CategoryTransactionType.category_id).where(CategoryTransactionType.transaction_type_id == txn_type.id)).all()]
    orphaned_categories = orphaned_groups = orphaned_contacts = budgets_total = 0
    for cat_id in category_ids:
        other_links = db.execute(
            select(func.count(CategoryTransactionType.id)).where(CategoryTransactionType.category_id == cat_id, CategoryTransactionType.transaction_type_id != txn_type.id)
        ).scalar_one()
        if other_links == 0:
            category = db.execute(select(Category).where(Category.id == cat_id)).scalar_one()
            sub = check_category_deletion(db, user_id, category)
            if sub.blocked:
                return DeletionPlan(blocked=True, reason=f"Cannot delete transaction type '{txn_type.name}': {sub.reason}")
            orphaned_categories += 1
            orphaned_groups += sub.cascade.get("orphaned groups", 0)
            orphaned_contacts += sub.cascade.get("orphaned contacts", 0)
            budgets_total += sub.cascade.get("budgets", 0)

    return DeletionPlan(cascade={"orphaned categories": orphaned_categories, "orphaned groups": orphaned_groups, "orphaned contacts": orphaned_contacts, "budgets": budgets_total})


def delete_transaction_type(db: Session, user_id: int, txn_type: TransactionType) -> None:
    category_ids = [row[0] for row in db.execute(select(CategoryTransactionType.category_id).where(CategoryTransactionType.transaction_type_id == txn_type.id)).all()]
    db.query(CategoryTransactionType).filter(CategoryTransactionType.transaction_type_id == txn_type.id).delete(synchronize_session=False)
    db.flush()
    for cat_id in category_ids:
        other_links = db.execute(select(func.count(CategoryTransactionType.id)).where(CategoryTransactionType.category_id == cat_id)).scalar_one()
        if other_links == 0:
            category = db.execute(select(Category).where(Category.id == cat_id)).scalar_one()
            delete_category(db, user_id, category)
    db.flush()
    db.delete(txn_type)


# --------------------------------------------------------------------- #
# Account (unchanged by Option B) — cascades to its payment methods
# --------------------------------------------------------------------- #
def check_account_deletion(db: Session, user_id: int, account: Account) -> DeletionPlan:
    if account.is_system or account.is_default:
        return DeletionPlan(blocked=True, reason="The default/system account cannot be deleted.")
    stmt = select(func.count(Transaction.id)).where(
        Transaction.is_active == True,  # noqa: E712
        Transaction.user_id == user_id,
        (Transaction.from_account_id == account.id) | (Transaction.to_account_id == account.id),
    )
    txn_count = db.execute(stmt).scalar_one()
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete account '{account.name}': used by {txn_count} transaction(s). Delete or reassign those transactions first.")
    pm_count = db.execute(select(func.count(PaymentMethod.id)).where(PaymentMethod.account_id == account.id, PaymentMethod.user_id == user_id)).scalar_one()
    return DeletionPlan(cascade={"payment methods": pm_count})


def delete_account(db: Session, user_id: int, account: Account) -> None:
    pms = list(db.execute(select(PaymentMethod).where(PaymentMethod.account_id == account.id, PaymentMethod.user_id == user_id)).scalars().all())
    for pm in pms:
        db.delete(pm)
    db.flush()
    db.delete(account)


# --------------------------------------------------------------------- #
# PaymentMethod (leaf, unchanged)
# --------------------------------------------------------------------- #
def check_payment_method_deletion(db: Session, user_id: int, pm: PaymentMethod) -> DeletionPlan:
    txn_count = _active_txn_count(db, payment_method_id=pm.id, user_id=user_id)
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete payment method '{pm.name}': used by {txn_count} transaction(s). Delete or reassign those transactions first.")
    return DeletionPlan()


def delete_payment_method(db: Session, pm: PaymentMethod) -> None:
    db.delete(pm)


# --------------------------------------------------------------------- #
# Priority (unchanged) — Transaction FK mandatory -> blocks; Goal FK
# nullable -> cascade-unlink
# --------------------------------------------------------------------- #
def check_priority_deletion(db: Session, user_id: int, priority: Priority) -> DeletionPlan:
    if priority.user_id is None:
        return DeletionPlan(blocked=True, reason="System default priorities cannot be deleted.")
    txn_count = _active_txn_count(db, priority_id=priority.id, user_id=user_id)
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete priority '{priority.name}': used by {txn_count} transaction(s). Delete or reassign those transactions first.")
    goal_count = db.execute(select(func.count(Goal.id)).where(Goal.priority_id == priority.id, Goal.user_id == user_id)).scalar_one()
    return DeletionPlan(cascade={"goals (priority will be cleared, not deleted)": goal_count})


def delete_priority(db: Session, user_id: int, priority: Priority) -> None:
    goals = list(db.execute(select(Goal).where(Goal.priority_id == priority.id, Goal.user_id == user_id)).scalars().all())
    for g in goals:
        g.priority_id = None
    db.flush()
    db.delete(priority)


# --------------------------------------------------------------------- #
# CreditPurchase — blocked if active settlement transactions still
# reference it (mirrors the loan-chain rule in spirit, kept as fully
# separate code so the two systems never intertwine)
# --------------------------------------------------------------------- #
def check_credit_purchase_deletion(db: Session, user_id: int, cp: CreditPurchase) -> DeletionPlan:
    txn_count = _active_txn_count(db, credit_purchase_id=cp.id, user_id=user_id)
    if txn_count:
        return DeletionPlan(blocked=True, reason=f"Cannot delete this credit purchase: {txn_count} payment transaction(s) are recorded against it. Delete those first.")
    return DeletionPlan()


def delete_credit_purchase(db: Session, cp: CreditPurchase) -> None:
    db.delete(cp)


# --------------------------------------------------------------------- #
# Budget / Goal — leaf nodes
# --------------------------------------------------------------------- #
def check_budget_deletion(budget: Budget) -> DeletionPlan:
    if budget.spent_amount and budget.spent_amount > 0:
        return DeletionPlan(cascade={f"tracked spending record ({budget.spent_amount})": 1})
    return DeletionPlan()


def check_goal_deletion(goal: Goal) -> DeletionPlan:
    # An active goal that still holds savings must be released first.
    # Empty goals and completed goals can be deleted (Undo from History restores them).
    if goal.saved_amount and goal.saved_amount > 0 and not goal.is_completed:
        return DeletionPlan(blocked=True, reason="This goal still has savings. Release the savings first, then delete the goal.")
    return DeletionPlan()
