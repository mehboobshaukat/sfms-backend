from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import transaction_type_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.schemas.transaction_type import TransactionTypeCreate, TransactionTypeUpdate, TransactionTypeOut

router = APIRouter(prefix="/transaction-types", tags=["Transaction Types"])


@router.get("", response_model=list[TransactionTypeOut])
def list_transaction_types(
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the user's custom types PLUS the 15 system defaults (user_id=NULL)."""
    return transaction_type_crud.list(db, current_user.id, active_only=active_only)




@router.get("/paged", response_model=PaginatedResponse[TransactionTypeOut])
def paged_transaction_types(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return transaction_type_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=TransactionTypeOut, status_code=status.HTTP_201_CREATED)
def create_transaction_type(
    payload: TransactionTypeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump()
    txn_type = transaction_type_crud.create(db, current_user.id, data)
    history_service.log_entry(db, current_user.id, "transaction_type", txn_type.slug, txn_type.name, action="create", after_snapshot=data)
    db.commit()
    return txn_type


@router.put("/{slug}", response_model=TransactionTypeOut)
def update_transaction_type(
    slug: str,
    payload: TransactionTypeUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = transaction_type_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name, "nature": before.nature, "icon": before.icon, "color": before.color}
    txn_type = transaction_type_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "transaction_type", txn_type.slug, txn_type.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return txn_type


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction_type(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the type everywhere. Blocked outright if it (or
    anything under it) is still used by a transaction. If deleting it
    would orphan categories/groups/contacts/budgets beneath it, the first
    call returns a 409 describing exactly what will be removed —
    resubmit with ?confirm=true to proceed.
    """
    txn_type = transaction_type_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_transaction_type_deletion(db, current_user.id, txn_type)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": txn_type.name, "nature": txn_type.nature, "icon": txn_type.icon, "color": txn_type.color}
    label, tt_slug = txn_type.name, txn_type.slug
    deletion_guard.delete_transaction_type(db, current_user.id, txn_type)
    history_service.log_entry(db, current_user.id, "transaction_type", tt_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
