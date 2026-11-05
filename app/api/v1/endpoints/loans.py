from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import contact_crud
from app.models.user import User
from app.models.loan import Loan
from app.schemas.loan import LoanOut

router = APIRouter(prefix="/loans", tags=["Loans"])


@router.get("", response_model=list[LoanOut])
def list_loans(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Loan).filter(Loan.user_id == current_user.id).order_by(Loan.id.desc()).all()


@router.get("/active", response_model=list[LoanOut])
def list_active_loans(
    type: Literal["received", "given"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Feeds the Loan Repaid / Loan Collected dropdown: Loan Repaid -> type=received, Loan Collected -> type=given."""
    return (
        db.query(Loan)
        .filter(Loan.user_id == current_user.id, Loan.loan_type == type, Loan.is_completed == False)  # noqa: E712
        .order_by(Loan.id.desc())
        .all()
    )


@router.get("/contact/{contact_slug}", response_model=list[LoanOut])
def list_loans_by_contact(
    contact_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contact = contact_crud.get(db, current_user.id, contact_slug)
    return (
        db.query(Loan)
        .filter(Loan.user_id == current_user.id, Loan.contact_id == contact.id)
        .order_by(Loan.id.desc())
        .all()
    )


@router.get("/{loan_code}", response_model=LoanOut)
def get_loan(
    loan_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    loan = db.query(Loan).filter(Loan.loan_code == loan_code, Loan.user_id == current_user.id).first()
    if not loan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan not found.")
    return loan
