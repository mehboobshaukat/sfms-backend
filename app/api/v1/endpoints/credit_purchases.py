from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud import credit_purchase_service, deletion_guard, history_service
from app.models.user import User
from app.schemas.credit_purchase import CreditPurchaseCreate, CreditPurchaseUpdate, CreditPurchaseOut

router = APIRouter(prefix="/credit-purchases", tags=["Credit Purchases"])


@router.get("", response_model=list[CreditPurchaseOut])
def list_credit_purchases(
    credit_type: Literal["received", "given"] | None = None,
    is_completed: bool | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return credit_purchase_service.list_credit_purchases(db, current_user.id, credit_type, is_completed)


@router.get("/outstanding-totals")
def outstanding_totals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard widget: total still owed (received) and total still receivable (given) — visible immediately, no transaction required."""
    return credit_purchase_service.outstanding_totals(db, current_user.id)


@router.post("", response_model=CreditPurchaseOut, status_code=status.HTTP_201_CREATED)
def create_credit_purchase(
    payload: CreditPurchaseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Records an item received or given on credit. Deliberately does NOT
    create a Transaction or touch any account balance — only a liability
    (received) or receivable (given) is recorded, visible immediately via
    remaining_amount. Money only moves once a settlement Transaction
    ("Item Payment Made"/"Item Payment Received") is created against it.
    """
    cp = credit_purchase_service.create_credit_purchase(db, current_user.id, payload)
    history_service.log_entry(db, current_user.id, "credit_purchase", cp.slug, cp.item_description, action="create", after_snapshot=payload.model_dump(mode="json"))
    db.commit()
    db.refresh(cp)
    return cp


@router.get("/{slug}", response_model=CreditPurchaseOut)
def get_credit_purchase(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return credit_purchase_service.get_credit_purchase(db, current_user.id, slug)


@router.put("/{slug}", response_model=CreditPurchaseOut)
def update_credit_purchase(
    slug: str,
    payload: CreditPurchaseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Only item_description, category, and due_date can be changed —
    credit_type, contact, and total_amount are anchors for
    remaining_amount and any settlements already recorded, so they stay
    fixed. Delete (if unsettled) and re-record instead if one of those
    was wrong.
    """
    before = credit_purchase_service.get_credit_purchase(db, current_user.id, slug)
    before_snapshot = {"item_description": before.item_description, "category_slug": before.category_slug, "due_date": before.due_date.isoformat() if before.due_date else None}
    cp = credit_purchase_service.update_credit_purchase(db, current_user.id, slug, payload)
    history_service.log_entry(db, current_user.id, "credit_purchase", cp.slug, cp.item_description, action="update", before_snapshot=before_snapshot)
    db.commit()
    db.refresh(cp)
    return cp


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credit_purchase(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently deletes the credit purchase. Blocked if any settlement transactions are recorded against it — delete those first."""
    cp = credit_purchase_service.get_credit_purchase(db, current_user.id, slug)
    plan = deletion_guard.check_credit_purchase_deletion(db, current_user.id, cp)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {
        "credit_type": cp.credit_type, "contact_slug": cp.contact_slug, "category_slug": cp.category_slug,
        "item_description": cp.item_description, "total_amount": float(cp.total_amount),
        "purchase_date": cp.purchase_date.isoformat(), "due_date": cp.due_date.isoformat() if cp.due_date else None,
    }
    label, cp_slug = cp.item_description, cp.slug
    deletion_guard.delete_credit_purchase(db, cp)
    history_service.log_entry(db, current_user.id, "credit_purchase", cp_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
