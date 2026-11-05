"""
Per-user seed data, applied at registration time.

System-wide defaults (TransactionType, Priority with user_id=NULL) are
seeded via app/utils/seed_system_defaults.py (also run automatically at
app startup — see app/main.py).

The seed helpers in this module are deliberately idempotent. This matters
for the one-time backfill migration added after default categories were
introduced: existing users can safely receive any missing defaults without
duplicating categories, accounts, or category/type links they already have.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.category import Category
from app.models.payment_method import PaymentMethod
from app.models.links import CategoryTransactionType
from app.models.transaction_type import TransactionType
from app.utils.slugs import generate_slug, normalize_name


# Default categories requested for every newly registered user. Category is
# reusable in the existing Option-B many-to-many structure, so repeated names
# (Family, Friend, Relative, Other, Item Payment, Family Support) are created
# once and linked to every listed transaction type.
DEFAULT_USER_CATEGORIES: dict[str, list[str]] = {
    "Income": [
        "Salary", "Stipend", "Freelancing", "Business", "Gift",
        "Family Support", "Investment", "Other Income",
    ],
    "Expense": [
        "Food", "Transport", "Fuel", "Stationery", "Internet",
        "Mobile Package", "Shopping", "Medical", "Education",
        "Certification", "Utilities", "Entertainment", "Other Expense",
    ],
    "Item Payment Received": ["Item Payment"],
    "Item Payment Made": ["Item Payment"],
    "Refund": ["General Refund"],
    "Charges": ["Bank Charges", "Transfer Charges", "Other Charges"],
    "Loan Received": ["Family", "Friend", "Relative", "Other"],
    "Loan Repaid": ["Family", "Friend", "Relative", "Other"],
    "Loan Given": ["Family", "Friend", "Relative", "Other"],
    "Loan Collected": ["Family", "Friend", "Relative", "Other"],
    "Give Support": ["Family", "Friend", "Relative", "Other"],
    "Take Support": ["Family", "Friend", "Relative", "Other"],
    "Deposit": ["General Deposit"],
    "Withdraw": ["General Withdrawal"],
    "Transfer": ["General Transfer"],
    "Other": ["General"],
}


def seed_user_accounts(db: Session, user_id: int) -> None:
    """Ensure the two original per-user accounts exist without duplicating them."""
    existing_names = {
        normalize_name(name)
        for name in db.execute(select(Account.name).where(Account.user_id == user_id)).scalars().all()
    }

    if normalize_name("Cash") not in existing_names:
        db.add(Account(
            user_id=user_id,
            name="Cash",
            account_type="cash",
            is_default=True,
            current_balance=0,
            slug=generate_slug(db, Account, "account"),
        ))
        db.flush()

    if normalize_name("External") not in existing_names:
        db.add(Account(
            user_id=user_id,
            name="External",
            account_type="external",
            is_system=True,
            current_balance=0,
            slug=generate_slug(db, Account, "account"),
        ))
        db.flush()



def seed_user_payment_methods(db: Session, user_id: int) -> None:
    """Ensure a sensible default payment method exists for the default Cash account.

    Payment methods are intentionally account-scoped in the existing model.
    A new user already receives a default Cash account, so seeding one `Cash`
    payment method removes the first-transaction dead end without changing the
    surrounding payment-method structure or inventing methods for other accounts.
    """
    cash_account = db.execute(
        select(Account).where(
            Account.user_id == user_id,
            Account.name == "Cash",
        )
    ).scalar_one_or_none()
    if cash_account is None:
        return

    existing = db.execute(
        select(PaymentMethod).where(
            PaymentMethod.user_id == user_id,
            PaymentMethod.name == "Cash",
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(PaymentMethod(
            user_id=user_id,
            account_id=cash_account.id,
            name="Cash",
            slug=generate_slug(db, PaymentMethod, "payment_method"),
        ))
        db.flush()


def seed_user_categories(db: Session, user_id: int) -> None:
    """Ensure all requested default categories and links exist for one user.

    This function used to assume it would only ever run once, during a brand-new
    registration. Existing accounts therefore never received the new defaults.
    It now reuses any same-name category already owned by the user and creates
    only missing CategoryTransactionType links, making it safe for registration
    and for the one-time existing-user backfill migration.
    """
    required_type_names = set(DEFAULT_USER_CATEGORIES)
    transaction_types = db.execute(
        select(TransactionType).where(
            TransactionType.user_id.is_(None),
            TransactionType.name.in_(required_type_names),
        )
    ).scalars().all()
    type_by_name = {transaction_type.name: transaction_type for transaction_type in transaction_types}

    missing = sorted(required_type_names - set(type_by_name))
    if missing:
        raise RuntimeError(
            "System transaction types must be seeded before user categories. Missing: " + ", ".join(missing)
        )

    # Reuse any pre-existing same-name categories for this user. This preserves
    # the current Category table and many-to-many structure exactly as-is.
    existing_categories = db.execute(
        select(Category).where(Category.user_id == user_id)
    ).scalars().all()
    category_by_normalized_name: dict[str, Category] = {
        normalize_name(category.name): category for category in existing_categories
    }

    # Read all current links once so we can add only missing pairs.
    existing_category_ids = [category.id for category in existing_categories]
    existing_links: set[tuple[int, int]] = set()
    if existing_category_ids:
        existing_links = {
            (row[0], row[1])
            for row in db.execute(
                select(
                    CategoryTransactionType.category_id,
                    CategoryTransactionType.transaction_type_id,
                ).where(CategoryTransactionType.category_id.in_(existing_category_ids))
            ).all()
        }

    for transaction_type_name, category_names in DEFAULT_USER_CATEGORIES.items():
        transaction_type = type_by_name[transaction_type_name]

        for category_name in category_names:
            normalized_name = normalize_name(category_name)
            category = category_by_normalized_name.get(normalized_name)

            if category is None:
                category = Category(
                    user_id=user_id,
                    name=category_name,
                    slug=generate_slug(db, Category, "category"),
                )
                db.add(category)
                db.flush()
                category_by_normalized_name[normalized_name] = category

            link_key = (category.id, transaction_type.id)
            if link_key not in existing_links:
                db.add(CategoryTransactionType(
                    category_id=category.id,
                    transaction_type_id=transaction_type.id,
                ))
                existing_links.add(link_key)

    db.flush()


def seed_user_defaults(db: Session, user_id: int) -> None:
    seed_user_accounts(db, user_id)
    seed_user_categories(db, user_id)
    seed_user_payment_methods(db, user_id)
