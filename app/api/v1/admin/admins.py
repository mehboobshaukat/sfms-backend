"""Super-admin-only: who is an admin. Admins cannot call anything here."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_super_admin
from app.core.limiter import limiter
from app.core.security import verify_password
from app.crud import admin_user_service as svc
from app.models.user import User
from app.schemas.admin import AdminUserOut, ConfirmPasswordRequest, PromoteAdminRequest

router = APIRouter(prefix="/admins", tags=["Admin - Admins (super admin)"], dependencies=[Depends(require_super_admin)])


def _confirm_password(actor: User, password: str) -> None:
    """Role changes re-check the super admin's password (stolen session != role control)."""
    if not verify_password(password, actor.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password is incorrect.")


@router.get("", response_model=list[AdminUserOut])
def list_admins(db: Session = Depends(get_db)):
    return [svc.to_out(u) for u in svc.list_admins(db)]


@router.post("", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
def promote_admin(
    request: Request, payload: PromoteAdminRequest,
    actor: User = Depends(require_super_admin), db: Session = Depends(get_db),
):
    _confirm_password(actor, payload.password)
    target = svc.get_by_username(db, payload.username)
    return svc.to_out(svc.promote_to_admin(db, actor, target, get_remote_address(request)))


@router.post("/{username}/remove", response_model=AdminUserOut)
@limiter.limit("10/hour")
def remove_admin(
    request: Request, username: str, payload: ConfirmPasswordRequest,
    actor: User = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """Removes adminship (the account stays, as a normal user). POST + body so the
    password confirmation travels in the body, not the URL."""
    _confirm_password(actor, payload.password)
    target = svc.get_by_username(db, username)
    return svc.to_out(svc.demote_admin(db, actor, target, get_remote_address(request)))
