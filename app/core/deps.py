"""
Reusable FastAPI dependencies: current authenticated user, DB session re-export.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.roles import ADMIN_ROLES, ROLE_SUPER_ADMIN
from app.core.security import decode_token
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=True)


def _resolve_user_from_token(credentials: HTTPAuthorizationCredentials, db: Session) -> User:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id = int(payload.get("sub"))
    except (InvalidTokenError, ValueError, TypeError):
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    if not user.email_confirmed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required")
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = _resolve_user_from_token(credentials, db)
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is blocked")
    return user


def get_current_user_allow_pending_deletion(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Same as get_current_user but does NOT reject a user whose account is
    scheduled for deletion (is_deleted=True, still inside the 30-day
    window). Needed because get_current_user's blanket is_deleted check
    would otherwise make POST /users/me/cancel-deletion permanently
    unreachable — a user can never cancel a deletion they're blocked from
    authenticating for in the first place. Still rejects blocked accounts
    and, implicitly, any account already hard-purged (no row = 401 same
    as before).
    """
    user = _resolve_user_from_token(credentials, db)
    if user.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is blocked")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Admin layer guard (admin or super_admin). Role comes from the DB row, not the token."""
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return current_user


def require_super_admin(current_user: User = Depends(require_admin)) -> User:
    if current_user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return current_user


# Backwards-compatible name (previously checked role == "admin" only, and was unused).
get_current_active_admin = require_admin
