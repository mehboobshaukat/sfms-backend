from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.transaction_type import TransactionType
from app.models.category import Category
from app.models.group import Group
from app.models.contact import Contact
from app.models.links import CategoryTransactionType, CategoryGroup, GroupContact
from app.models.account import Account
from app.models.payment_method import PaymentMethod
from app.models.priority import Priority

transaction_type_crud = CRUDBase(TransactionType, "transaction_type", include_system_default=True)
category_crud = CRUDBase(Category, "category")
group_crud = CRUDBase(Group, "group")
contact_crud = CRUDBase(Contact, "contact")
account_crud = CRUDBase(Account, "account")
payment_method_crud = CRUDBase(PaymentMethod, "payment_method")
priority_crud = CRUDBase(Priority, "priority", include_system_default=True)


# --------------------------------------------------------------------- #
# Link-table helpers (backend redesign: Category/Group/Contact are
# standalone + reusable — see models/links.py — so "does X belong to Y"
# is now "is there a link row between X and Y", not a single owning FK).
# All functions here take internal integer ids (already resolved from
# slugs by the caller) since that's what the link tables key on.
# --------------------------------------------------------------------- #
def link_category_to_type(db: Session, category_id: int, transaction_type_id: int) -> None:
    exists = db.execute(
        select(CategoryTransactionType.id).where(
            CategoryTransactionType.category_id == category_id,
            CategoryTransactionType.transaction_type_id == transaction_type_id,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(CategoryTransactionType(category_id=category_id, transaction_type_id=transaction_type_id))
        db.commit()


def link_group_to_category(db: Session, group_id: int, category_id: int) -> None:
    exists = db.execute(
        select(CategoryGroup.id).where(CategoryGroup.category_id == category_id, CategoryGroup.group_id == group_id)
    ).scalar_one_or_none()
    if exists is None:
        db.add(CategoryGroup(category_id=category_id, group_id=group_id))
        db.commit()


def link_contact_to_group(db: Session, contact_id: int, group_id: int) -> None:
    exists = db.execute(
        select(GroupContact.id).where(GroupContact.group_id == group_id, GroupContact.contact_id == contact_id)
    ).scalar_one_or_none()
    if exists is None:
        db.add(GroupContact(group_id=group_id, contact_id=contact_id))
        db.commit()


def assert_category_linked_to_type(db: Session, user_id: int, category_id: int, transaction_type_id: int) -> Category:
    category = db.execute(select(Category).where(Category.id == category_id, Category.user_id == user_id)).scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    link = db.execute(
        select(CategoryTransactionType.id).where(
            CategoryTransactionType.category_id == category_id,
            CategoryTransactionType.transaction_type_id == transaction_type_id,
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This category isn't linked to the selected transaction type yet. Link it first, or choose a different category.",
        )
    return category


def assert_group_linked_to_category(db: Session, user_id: int, group_id: int, category_id: int) -> Group:
    group = db.execute(select(Group).where(Group.id == group_id, Group.user_id == user_id)).scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    link = db.execute(
        select(CategoryGroup.id).where(CategoryGroup.category_id == category_id, CategoryGroup.group_id == group_id)
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This group isn't linked to the selected category yet.")
    return group


def _category_has_group_links(db: Session, category_id: int) -> bool:
    return db.execute(
        select(CategoryGroup.id).where(CategoryGroup.category_id == category_id).limit(1)
    ).scalar_one_or_none() is not None


def assert_contact_valid_for_category(db: Session, user_id: int, contact_id: int, category_id: int, group_id: int | None = None) -> Contact:
    """Validate a transaction contact without making first-time setup painful.

    Contact remains REQUIRED for loan transactions in transaction_service, but
    Group remains an optional classification layer. If a category has no groups
    linked yet (the normal state for newly seeded default categories), any active
    contact owned by the user is valid. Once the user intentionally links groups
    to that category, the existing category/group membership rules apply.
    """
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.user_id == user_id)
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    if not contact.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This contact is inactive.")

    if group_id is not None:
        link = db.execute(
            select(GroupContact.id).where(GroupContact.group_id == group_id, GroupContact.contact_id == contact_id)
        ).scalar_one_or_none()
        if not link:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This contact doesn't belong to the selected group.")
        return contact

    # No category groups configured yet: treat Group as optional and allow any
    # active contact for this user. This keeps the reusable link structure but
    # removes a hidden setup prerequisite from the transaction form.
    if not _category_has_group_links(db, category_id):
        return contact

    stmt = (
        select(GroupContact.id)
        .join(CategoryGroup, CategoryGroup.group_id == GroupContact.group_id)
        .where(CategoryGroup.category_id == category_id, GroupContact.contact_id == contact_id)
    )
    exists = db.execute(stmt).scalar_one_or_none()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This contact isn't linked to any group under the selected category.",
        )
    return contact


def contacts_for_category(db: Session, user_id: int, category_id: int, group_id: int | None = None) -> list[Contact]:
    """Return contacts usable for a category.

    With an explicit group, preserve the existing group filter. With category
    groups configured, return their union. With no category groups yet, return
    all active contacts so a newly registered user can create a loan after only
    creating the counterparty contact — no separate group-link setup required.
    """
    if group_id is not None:
        stmt = (
            select(Contact)
            .where(Contact.user_id == user_id, Contact.is_active == True)  # noqa: E712
            .join(GroupContact, GroupContact.contact_id == Contact.id)
            .where(GroupContact.group_id == group_id)
        )
        return list(db.execute(stmt.distinct()).scalars().all())

    if not _category_has_group_links(db, category_id):
        return list(
            db.execute(
                select(Contact).where(Contact.user_id == user_id, Contact.is_active == True).order_by(Contact.name)  # noqa: E712
            ).scalars().all()
        )

    stmt = (
        select(Contact)
        .where(Contact.user_id == user_id, Contact.is_active == True)  # noqa: E712
        .join(GroupContact, GroupContact.contact_id == Contact.id)
        .join(CategoryGroup, CategoryGroup.group_id == GroupContact.group_id)
        .where(CategoryGroup.category_id == category_id)
    )
    return list(db.execute(stmt.distinct()).scalars().all())


def assert_account_owned(db: Session, user_id: int, account_id: int) -> Account:
    account = db.execute(select(Account).where(Account.id == account_id, Account.user_id == user_id)).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


def assert_payment_method_matches_account(db: Session, user_id: int, payment_method_id: int, account_id: int) -> PaymentMethod:
    pm = db.execute(
        select(PaymentMethod).where(PaymentMethod.id == payment_method_id, PaymentMethod.user_id == user_id)
    ).scalar_one_or_none()
    if not pm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment method not found")
    if pm.account_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payment_method does not belong to the selected account.",
        )
    return pm


def get_or_create_external_account(db: Session, user_id: int) -> Account:
    statement = select(Account).where(
        Account.user_id == user_id,
        Account.is_system == True,  # noqa: E712
        Account.account_type == "external",
    )
    ext = db.execute(statement).scalar_one_or_none()
    if not ext:
        from app.utils.slugs import generate_slug

        ext = Account(
            user_id=user_id,
            name="External",
            account_type="external",
            is_system=True,
            is_default=False,
            current_balance=0,
            slug=generate_slug(db, Account, "account"),
        )
        db.add(ext)
        # Do not commit here: this account creation must share the caller's
        # transaction with the financial transaction and its history.
        db.flush()
    return ext
