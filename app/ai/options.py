"""The user's own lookup lists (types, categories, accounts, ...) for the AI flow."""
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.category import Category
from app.models.contact import Contact
from app.models.links import CategoryGroup, CategoryTransactionType, GroupContact
from app.models.payment_method import PaymentMethod
from app.models.priority import Priority
from app.models.transaction_type import TransactionType
from app.models.user import User
from app.models.loan import Loan
from app.models.credit_purchase import CreditPurchase


def available_options(db: Session, user_id: int):
    types = db.execute(select(TransactionType).where(
        or_(TransactionType.user_id == user_id, TransactionType.user_id.is_(None)),
        TransactionType.is_active.is_(True),
    )).scalars().all()
    categories = db.execute(select(Category).where(Category.user_id == user_id, Category.is_active.is_(True))).scalars().all()
    ids = [c.id for c in categories]
    linked = db.execute(select(CategoryTransactionType.category_id, CategoryTransactionType.transaction_type_id)
                        .where(CategoryTransactionType.category_id.in_(ids))).all() if ids else []
    by_category = {}
    for c_id, t_id in linked:
        by_category.setdefault(c_id, set()).add(t_id)
    # A category with linked groups only accepts contacts that belong to those groups
    # (see lookups.assert_contact_valid_for_category). None = no group links = any contact.
    group_rows = db.execute(select(CategoryGroup.category_id, CategoryGroup.group_id)
                            .where(CategoryGroup.category_id.in_(ids))).all() if ids else []
    groups_by_category = {}
    for c_id, g_id in group_rows:
        groups_by_category.setdefault(c_id, set()).add(g_id)
    members = {}
    all_groups = {g for gs in groups_by_category.values() for g in gs}
    for g_id, contact_id in (db.execute(select(GroupContact.group_id, GroupContact.contact_id)
                                        .where(GroupContact.group_id.in_(all_groups))).all() if all_groups else []):
        members.setdefault(g_id, set()).add(contact_id)
    accounts = db.execute(select(Account).where(Account.user_id == user_id, Account.is_active.is_(True), Account.is_system.is_(False))).scalars().all()
    payments = db.execute(select(PaymentMethod).where(PaymentMethod.user_id == user_id, PaymentMethod.is_active.is_(True))).scalars().all()
    priorities = db.execute(select(Priority).where(or_(Priority.user_id == user_id, Priority.user_id.is_(None)), Priority.is_active.is_(True))).scalars().all()
    contacts = db.execute(select(Contact).where(Contact.user_id == user_id, Contact.is_active.is_(True))).scalars().all()
    account_by_id = {a.id: a.slug for a in accounts}
    contact_slug_by_id = {c.id: c.slug for c in contacts}

    def contact_slugs_for(category_id):
        groups = groups_by_category.get(category_id)
        if not groups:
            return None
        allowed = set().union(*(members.get(g, set()) for g in groups))
        return sorted(contact_slug_by_id[i] for i in allowed if i in contact_slug_by_id)

    loans = db.execute(select(Loan).where(Loan.user_id == user_id, Loan.is_completed.is_(False),
                                         Loan.remaining_amount > 0)).scalars().all()
    purchases = db.execute(select(CreditPurchase).where(CreditPurchase.user_id == user_id,
                              CreditPurchase.is_completed.is_(False), CreditPurchase.remaining_amount > 0)).scalars().all()
    user = db.get(User, user_id)
    return {
        "currency_code": user.currency_code if user else "PKR",
        "types": [{"slug": x.slug, "name": x.name, "nature": x.nature} for x in types],
        "categories": [{"slug": x.slug, "name": x.name, "type_slugs": [t.slug for t in types if t.id in by_category.get(x.id, set())],
                       "contact_slugs": contact_slugs_for(x.id)} for x in categories],
        "accounts": [{"slug": x.slug, "name": x.name, "is_default": x.is_default} for x in accounts],
        "payment_methods": [{"slug": x.slug, "name": x.name, "account_slug": account_by_id.get(x.account_id)} for x in payments],
        "priorities": [{"slug": x.slug, "name": x.name, "is_default": x.is_default} for x in priorities],
        "contacts": [{"slug": x.slug, "name": x.name} for x in contacts],
        "loans": [{"slug": x.loan_code, "name": x.loan_code, "loan_type": x.loan_type,
                   "contact_slug": contact_slug_by_id.get(x.contact_id)} for x in loans],
        "credit_purchases": [{"slug": x.slug, "name": x.item_description,
                              "credit_type": x.credit_type,
                              "contact_slug": contact_slug_by_id.get(x.contact_id)} for x in purchases],
    }
