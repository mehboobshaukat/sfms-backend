"""
CreditPurchase business logic — deliberately separate from the Loan
system (per explicit instruction: never mix the two, even though the
underlying mechanics are similar).

Creating a CreditPurchase has ZERO effect on account balances — no
Transaction row is created here at all. The liability (credit_type=
"received") or receivable (credit_type="given") is visible immediately
via `remaining_amount` the moment the row exists. Money only actually
moves when a settlement Transaction ("Item Payment Made" / "Item Payment
Received") is created against it — see transaction_service.py.
"""
from datetime import date as date_cls

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credit_purchase import CreditPurchase
from app.models.contact import Contact
from app.models.category import Category
from app.schemas.credit_purchase import CreditPurchaseCreate
from app.utils.slugs import generate_slug


def create_credit_purchase(db: Session, user_id: int, payload: CreditPurchaseCreate) -> CreditPurchase:
    contact = db.execute(select(Contact).where(Contact.slug == payload.contact_slug, Contact.user_id == user_id)).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")

    category = None
    if payload.category_slug:
        category = db.execute(select(Category).where(Category.slug == payload.category_slug, Category.user_id == user_id)).scalar_one_or_none()
        if not category:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    cp = CreditPurchase(
        user_id=user_id,
        slug=generate_slug(db, CreditPurchase, "credit_purchase"),
        credit_type=payload.credit_type,
        contact_id=contact.id,
        category_id=category.id if category else None,
        item_description=payload.item_description,
        total_amount=payload.total_amount,
        remaining_amount=payload.total_amount,
        purchase_date=payload.purchase_date,
        due_date=payload.due_date,
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)
    return cp


def get_credit_purchase(db: Session, user_id: int, slug: str) -> CreditPurchase:
    cp = db.execute(select(CreditPurchase).where(CreditPurchase.slug == slug, CreditPurchase.user_id == user_id)).scalar_one_or_none()
    if not cp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credit purchase not found")
    return cp


def update_credit_purchase(db: Session, user_id: int, slug: str, payload) -> CreditPurchase:
    cp = get_credit_purchase(db, user_id, slug)
    data = payload.model_dump(exclude_unset=True)
    if "category_slug" in data:
        cat_slug = data.pop("category_slug")
        if cat_slug:
            category = db.execute(select(Category).where(Category.slug == cat_slug, Category.user_id == user_id)).scalar_one_or_none()
            if not category:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
            cp.category_id = category.id
        else:
            cp.category_id = None
    for key, value in data.items():
        setattr(cp, key, value)
    db.commit()
    db.refresh(cp)
    return cp


def list_credit_purchases(db: Session, user_id: int, credit_type: str | None = None, is_completed: bool | None = None) -> list[CreditPurchase]:
    stmt = select(CreditPurchase).where(CreditPurchase.user_id == user_id)
    if credit_type is not None:
        stmt = stmt.where(CreditPurchase.credit_type == credit_type)
    if is_completed is not None:
        stmt = stmt.where(CreditPurchase.is_completed == is_completed)
    stmt = stmt.order_by(CreditPurchase.purchase_date.desc())
    return list(db.execute(stmt).scalars().all())


def outstanding_totals(db: Session, user_id: int) -> dict:
    """Powers the dashboard's "liability visible immediately" requirement —
    mirrors the Loans page's pending_received_total/pending_given_total pattern."""
    from sqlalchemy import func

    owed = db.execute(
        select(func.coalesce(func.sum(CreditPurchase.remaining_amount), 0)).where(
            CreditPurchase.user_id == user_id, CreditPurchase.credit_type == "received", CreditPurchase.is_completed == False  # noqa: E712
        )
    ).scalar_one()
    receivable = db.execute(
        select(func.coalesce(func.sum(CreditPurchase.remaining_amount), 0)).where(
            CreditPurchase.user_id == user_id, CreditPurchase.credit_type == "given", CreditPurchase.is_completed == False  # noqa: E712
        )
    ).scalar_one()
    return {"pending_owed_total": owed, "pending_receivable_total": receivable}
