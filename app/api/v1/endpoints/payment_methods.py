from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import payment_method_crud, account_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.schemas.payment_method import PaymentMethodCreate, PaymentMethodUpdate, PaymentMethodOut

router = APIRouter(prefix="/payment-methods", tags=["Payment Methods"])


@router.get("", response_model=list[PaymentMethodOut])
def list_payment_methods(
    account_slug: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Filter by ?account_slug= — payment methods are always tied to one account."""
    account_id = account_crud.get(db, current_user.id, account_slug).id if account_slug else None
    return payment_method_crud.list(db, current_user.id, active_only=active_only, account_id=account_id)




@router.get("/paged", response_model=PaginatedResponse[PaymentMethodOut])
def paged_payment_methods(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return payment_method_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=PaymentMethodOut, status_code=status.HTTP_201_CREATED)
def create_payment_method(
    payload: PaymentMethodCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = account_crud.get(db, current_user.id, payload.account_slug)  # validates ownership
    data = {"account_id": account.id, "name": payload.name}
    pm = payment_method_crud.create(db, current_user.id, data)
    history_service.log_entry(db, current_user.id, "payment_method", pm.slug, pm.name, action="create", after_snapshot={"account_slug": payload.account_slug, "name": payload.name})
    db.commit()
    return pm


@router.put("/{slug}", response_model=PaymentMethodOut)
def update_payment_method(
    slug: str,
    payload: PaymentMethodUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = payment_method_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name}
    pm = payment_method_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "payment_method", pm.slug, pm.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return pm


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payment_method(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently deletes the payment method. Blocked if any transaction still uses it."""
    pm = payment_method_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_payment_method_deletion(db, current_user.id, pm)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": pm.name, "account_id": pm.account_id}
    label, pm_slug = pm.name, pm.slug
    deletion_guard.delete_payment_method(db, pm)
    history_service.log_entry(db, current_user.id, "payment_method", pm_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
