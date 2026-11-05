from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import account_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.schemas.account import AccountCreate, AccountUpdate, AccountOut

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(
    active_only: bool = False,
    include_system: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hides the internal 'External' pseudo-account by default. Pass ?include_system=true to see it anyway."""
    accounts = account_crud.list(db, current_user.id, active_only=active_only)
    if not include_system:
        accounts = [a for a in accounts if not a.is_system]
    return accounts


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump()
    account = account_crud.create(db, current_user.id, data)
    history_service.log_entry(db, current_user.id, "account", account.slug, account.name, action="create", after_snapshot=data)
    db.commit()
    return account


@router.put("/{slug}", response_model=AccountOut)
def update_account(
    slug: str,
    payload: AccountUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = account_crud.get(db, current_user.id, slug)
    if before.is_system:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The system 'External' account cannot be edited.")
    before_snapshot = {"name": before.name, "account_number": before.account_number, "bank_name": before.bank_name, "branch": before.branch}
    account = account_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "account", account.slug, account.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return account


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the account. Blocked if the system/default account,
    or if any transaction still references it. If it has payment methods
    under it, the first call returns a 409 cascade summary — resubmit with
    ?confirm=true to proceed.
    """
    account = account_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_account_deletion(db, current_user.id, account)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": account.name, "account_type": account.account_type, "account_number": account.account_number, "bank_name": account.bank_name, "branch": account.branch, "current_balance": float(account.current_balance)}
    label, a_slug = account.name, account.slug
    deletion_guard.delete_account(db, current_user.id, account)
    history_service.log_entry(db, current_user.id, "account", a_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
