from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud import draft_service
from app.models.user import User
from app.schemas.transaction_draft import TransactionDraftCreate, TransactionDraftUpdate, TransactionDraftOut
from app.schemas.transaction import TransactionOut
from app.schemas.draft_bulk import BulkDraftSelection, BulkDraftConfirmResult, BulkDraftRejectResult

router = APIRouter(prefix="/transaction-drafts", tags=["AI Transaction Drafts"])


@router.get("", response_model=list[TransactionDraftOut])
def list_drafts(
    status_filter: str | None = "pending_review",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return draft_service.list_drafts(db, current_user.id, status_filter)


@router.post("", response_model=TransactionDraftOut, status_code=status.HTTP_201_CREATED)
def create_draft(
    payload: TransactionDraftCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    FOUNDATION ONLY: stages the raw voice transcript / bulk-pasted text.
    No AI model is called yet — parsed_fields/ambiguous_fields are empty
    until that integration is added (see app/crud/draft_service.py).
    """
    return draft_service.create_draft(db, current_user.id, payload)



@router.post("/bulk/confirm", response_model=BulkDraftConfirmResult)
def confirm_selected_drafts(
    payload: BulkDraftSelection,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Each selected draft becomes a standard transaction with its own code/history.

    All selected drafts commit together; a single failure rolls back the batch.
    """
    return draft_service.confirm_drafts_bulk(db, current_user.id, payload.draft_slugs)


@router.post("/bulk/reject", response_model=BulkDraftRejectResult)
def reject_selected_drafts(
    payload: BulkDraftSelection,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return draft_service.reject_drafts_bulk(db, current_user.id, payload.draft_slugs)

@router.get("/{slug}", response_model=TransactionDraftOut)
def get_draft(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return draft_service.get_draft(db, current_user.id, slug)


@router.patch("/{slug}", response_model=TransactionDraftOut)
def update_draft(
    slug: str,
    payload: TransactionDraftUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User corrects/fills in fields the AI flagged as ambiguous, before confirming."""
    return draft_service.update_draft_fields(db, current_user.id, slug, payload.parsed_fields)


@router.post("/{slug}/confirm", response_model=TransactionOut)
def confirm_draft(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Creates the real transaction from this draft, via the normal validated create_transaction() path."""
    return draft_service.confirm_draft(db, current_user.id, slug)


@router.post("/{slug}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_draft(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft_service.reject_draft(db, current_user.id, slug)
