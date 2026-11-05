"""User management for the admin layer.

Admins see account/status basics only - never financial records - and can only act on
regular users. Role changes are reserved for the super admin.
"""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_USER
from app.crud import admin_audit, auth_service
from app.models.refresh_token import RefreshToken
from app.models.user import User


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def derive_status(user: User) -> str:
    if user.is_deleted:
        return "pending_deletion"
    if user.is_blocked:
        return "blocked"
    locked = _utc(user.locked_until)
    if locked and locked > datetime.now(timezone.utc):
        return "locked"
    if not user.email_confirmed:
        return "unverified"
    return "active"


def to_out(user: User) -> dict:
    """Explicit whitelist of what the admin panel may see about a user."""
    return {
        "username": user.username, "email": user.email,
        "first_name": user.first_name, "last_name": user.last_name,
        "country": user.country, "country_name": user.country_name,
        "role": user.role, "status": derive_status(user),
        "email_confirmed": user.email_confirmed, "is_blocked": user.is_blocked,
        "blocked_at": user.blocked_at, "blocked_reason": user.blocked_reason,
        "locked_until": user.locked_until, "deletion_scheduled_at": user.deletion_scheduled_at,
        "created_at": user.created_at,
    }


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_users(db: Session, *, q: str | None, status_filter: str | None, role: str | None,
               page: int, page_size: int) -> tuple[int, list[User]]:
    filters = []
    if q:
        like = f"%{_escape_like(q.strip()[:100])}%"
        filters.append(or_(
            User.username.ilike(like, escape="\\"), User.email.ilike(like, escape="\\"),
            User.first_name.ilike(like, escape="\\"), User.last_name.ilike(like, escape="\\"),
        ))
    if role:
        filters.append(User.role == role)

    now = datetime.now(timezone.utc)
    not_locked = or_(User.locked_until.is_(None), User.locked_until <= now)
    if status_filter == "pending_deletion":
        filters.append(User.is_deleted.is_(True))
    elif status_filter == "blocked":
        filters += [User.is_deleted.is_(False), User.is_blocked.is_(True)]
    elif status_filter == "locked":
        filters += [User.is_deleted.is_(False), User.is_blocked.is_(False), User.locked_until > now]
    elif status_filter == "unverified":
        filters += [User.is_deleted.is_(False), User.is_blocked.is_(False), User.email_confirmed.is_(False), not_locked]
    elif status_filter == "active":
        filters += [User.is_deleted.is_(False), User.is_blocked.is_(False), User.email_confirmed.is_(True), not_locked]

    total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    rows = db.scalars(
        select(User).where(*filters).order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return total, list(rows)


def get_by_username(db: Session, username: str) -> User:
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    return user


def _assert_manageable(actor: User, target: User) -> None:
    if target.id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot perform this action on your own account.")
    if target.role == ROLE_SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The super admin account cannot be modified.")
    if actor.role != ROLE_SUPER_ADMIN and target.role != ROLE_USER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the super admin can manage other admins.")


def revoke_all_sessions(db: Session, user: User) -> int:
    return db.query(RefreshToken).filter(
        RefreshToken.user_id == user.id, RefreshToken.is_revoked.is_(False)
    ).update({RefreshToken.is_revoked: True}, synchronize_session=False)


def block_user(db: Session, actor: User, target: User, reason: str, ip: str | None) -> User:
    _assert_manageable(actor, target)
    if target.is_blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is already blocked.")
    target.is_blocked = True
    target.blocked_at = datetime.now(timezone.utc)
    target.blocked_reason = reason
    revoke_all_sessions(db, target)  # access tokens die immediately (get_current_user checks is_blocked)
    admin_audit.record(db, actor, "user.block", "user", target.username, detail=reason, ip=ip)
    db.commit()
    db.refresh(target)
    return target


def unblock_user(db: Session, actor: User, target: User, ip: str | None) -> User:
    _assert_manageable(actor, target)
    if not target.is_blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is not blocked.")
    target.is_blocked = False
    target.blocked_at = None
    target.blocked_reason = None
    admin_audit.record(db, actor, "user.unblock", "user", target.username, ip=ip)
    db.commit()
    db.refresh(target)
    return target


def unlock_user(db: Session, actor: User, target: User, ip: str | None) -> User:
    _assert_manageable(actor, target)
    target.locked_until = None
    target.failed_login_attempts = 0
    admin_audit.record(db, actor, "user.unlock", "user", target.username, ip=ip)
    db.commit()
    db.refresh(target)
    return target


def force_logout(db: Session, actor: User, target: User, ip: str | None) -> int:
    _assert_manageable(actor, target)
    revoked = revoke_all_sessions(db, target)
    admin_audit.record(db, actor, "user.revoke_sessions", "user", target.username, detail=f"{revoked} session(s)", ip=ip)
    db.commit()
    return revoked


def resend_verification(db: Session, actor: User, target: User, ip: str | None) -> None:
    _assert_manageable(actor, target)
    if target.email_confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account's email is already verified.")
    if target.is_deleted:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is pending deletion.")
    auth_service.resend_verification_email(db, target.email)  # commits; raises 503 if SMTP fails
    admin_audit.record(db, actor, "user.resend_verification", "user", target.username, ip=ip)
    db.commit()


# ------------------------------------------------------------- super admin only
def list_admins(db: Session) -> list[User]:
    return list(db.scalars(
        select(User).where(User.role.in_([ROLE_ADMIN, ROLE_SUPER_ADMIN]))
        .order_by(User.role.desc(), User.created_at)
    ).all())


def promote_to_admin(db: Session, actor: User, target: User, ip: str | None) -> User:
    _assert_manageable(actor, target)
    if target.role != ROLE_USER:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is already an admin.")
    if target.is_deleted or target.is_blocked or not target.email_confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only active, email-verified accounts can become admins.")
    target.role = ROLE_ADMIN
    admin_audit.record(db, actor, "admin.promote", "user", target.username, ip=ip)
    db.commit()
    db.refresh(target)
    return target


def demote_admin(db: Session, actor: User, target: User, ip: str | None) -> User:
    _assert_manageable(actor, target)
    if target.role != ROLE_ADMIN:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is not an admin.")
    target.role = ROLE_USER
    admin_audit.record(db, actor, "admin.demote", "user", target.username, ip=ip)
    db.commit()
    db.refresh(target)
    return target
