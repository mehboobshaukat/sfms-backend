from fastapi import APIRouter, Depends, Query, Request
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.limiter import limiter
from app.crud import admin_user_service as svc
from app.models.user import User
from app.schemas.admin import AdminUserOut, BlockUserRequest, UserStatus
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/users", tags=["Admin - Users"])


@router.get("", response_model=PaginatedResponse[AdminUserOut])
def list_users(
    q: str | None = Query(None, max_length=100),
    status_filter: UserStatus | None = Query(None, alias="status"),
    role: str | None = Query(None, pattern="^(user|admin|super_admin)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total, rows = svc.list_users(db, q=q, status_filter=status_filter, role=role, page=page, page_size=page_size)
    return {"items": [svc.to_out(u) for u in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/{username}", response_model=AdminUserOut)
def get_user(username: str, db: Session = Depends(get_db)):
    return svc.to_out(svc.get_by_username(db, username))


@router.post("/{username}/block", response_model=AdminUserOut)
def block_user(
    username: str, payload: BlockUserRequest, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    user = svc.block_user(db, admin, svc.get_by_username(db, username), payload.reason, get_remote_address(request))
    return svc.to_out(user)


@router.post("/{username}/unblock", response_model=AdminUserOut)
def unblock_user(
    username: str, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    return svc.to_out(svc.unblock_user(db, admin, svc.get_by_username(db, username), get_remote_address(request)))


@router.post("/{username}/unlock", response_model=AdminUserOut)
def unlock_user(
    username: str, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Clears a failed-login lockout."""
    return svc.to_out(svc.unlock_user(db, admin, svc.get_by_username(db, username), get_remote_address(request)))


@router.post("/{username}/revoke-sessions")
def revoke_sessions(
    username: str, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Force-logout: revokes every refresh token of the user."""
    revoked = svc.force_logout(db, admin, svc.get_by_username(db, username), get_remote_address(request))
    return {"revoked": revoked}


@router.post("/{username}/resend-verification", status_code=202)
@limiter.limit("10/hour")
def resend_verification(
    request: Request, username: str,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    svc.resend_verification(db, admin, svc.get_by_username(db, username), get_remote_address(request))
    return {"detail": "Verification email sent."}
