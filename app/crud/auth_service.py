"""
Authentication business logic — registration, login (with account lockout),
refresh-token rotation, email verification, forgot/reset password.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import HTTPException, status
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session
from jwt.exceptions import InvalidTokenError

from app.core.config import settings
from app.core.email import EmailDeliveryError, send_password_reset_email, send_verification_email
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.crud.seed import seed_user_defaults

logger = logging.getLogger(__name__)


def _hash_one_time_token(token: str) -> str:
    """Store only a deterministic SHA-256 digest of high-entropy one-time tokens.

    Verification links carry the raw random token; the database keeps only the
    digest so a database read leak cannot be used directly to verify accounts.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_email_verification_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, _hash_one_time_token(raw)



def register_user(
    db: Session,
    username: str,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    phone_number: str | None = None,
    profile_image_url: str | None = None,
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
    postal_code: str | None = None,
) -> User:
    username = username.lower()
    email = email.lower()

    if db.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")
    if db.execute(select(User).where(func.lower(User.username) == username)).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken.")

    verification_token, verification_token_hash = _new_email_verification_token()
    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone_number=phone_number,
        profile_image_url=profile_image_url,
        country=country,
        state=state,
        city=city,
        postal_code=postal_code,
        password_hash=hash_password(password),
        email_verify_token=verification_token_hash,
        email_verify_token_expiry=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    try:
        db.add(user)
        db.flush()  # get user.id
        seed_user_defaults(db, user.id)

        # Email verification is required for every new account. Send before
        # commit so a mail failure rolls back the registration instead of
        # leaving the user stuck with an unverified, inaccessible account.
        send_verification_email(
            recipient=user.email,
            first_name=user.first_name or user.username,
            token=verification_token,
        )

        db.commit()
        db.refresh(user)
        return user
    except EmailDeliveryError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email could not be sent right now. Please try again later.",
        ) from exc
    except Exception:
        db.rollback()
        raise


def _issue_token_pair(
    db: Session,
    user: User,
    *,
    commit: bool = True,
) -> tuple[str, str]:
    jti = secrets.token_urlsafe(24)

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id, jti)

    db.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
            ),
        )
    )

    if commit:
        db.commit()

    return access_token, refresh_token


def login_user(db: Session, email: str, password: str) -> tuple[str, str]:
    user = db.query(User).filter(func.lower(User.email) == email.lower()).first()
    if not user or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    now = datetime.now(timezone.utc)
    if user.locked_until:
        locked_until_utc = user.locked_until.replace(tzinfo=timezone.utc) if user.locked_until.tzinfo is None else user.locked_until
        if locked_until_utc > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account locked until {locked_until_utc.isoformat()} due to too many failed attempts.",
            )

    if user.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is blocked.")

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    if not user.email_confirmed:
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before signing in.",
        )

    # successful login resets counters
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    return _issue_token_pair(db, user)


def refresh_tokens(db: Session, refresh_token: str) -> tuple[str, str]:
    try:
        payload = decode_token(refresh_token)

        if payload.get("type") != "refresh":
            raise ValueError("Wrong token type")

        user_id = int(payload["sub"])
        jti = payload["jti"]

    except (InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    try:
        user = db.query(User).filter(User.id == user_id).first()

        if not user or user.is_blocked or not user.email_confirmed:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User is not active or email is unverified.",
            )

        # A scheduled-deletion account is deliberately allowed
        # to refresh its session during the cancellation grace period.

        result = db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.jti == jti,
                RefreshToken.user_id == user_id,
                RefreshToken.is_revoked.is_(False),
                RefreshToken.expires_at > datetime.now(timezone.utc),
            )
            .values(is_revoked=True)
        )

        if result.rowcount != 1:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token is invalid, expired, or already used.",
            )

        # Generate the replacement without committing yet.
        new_access_token, new_refresh_token = _issue_token_pair(
            db,
            user,
            commit=False,
        )

        # Atomically commit both the revocation and replacement.
        db.commit()

        return new_access_token, new_refresh_token

    except Exception:
        db.rollback()
        raise


def logout_user(db: Session, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
        jti = payload.get("jti")
    except InvalidTokenError:
        return  # already invalid; nothing to revoke
    if jti:
        stored = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
        if stored:
            stored.is_revoked = True
            db.commit()


def verify_email(db: Session, token: str) -> User:
    token = (token or "").strip()
    if len(token) < 20 or len(token) > 512:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link.")

    token_hash = _hash_one_time_token(token)
    # New tokens are stored hashed. The raw-token fallback keeps verification
    # links issued by the immediately previous release usable until they expire.
    user = db.query(User).filter(
        (User.email_verify_token == token_hash) | (User.email_verify_token == token)
    ).first()
    if not user or not user.email_verify_token_expiry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link.")

    expiry = user.email_verify_token_expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < datetime.now(timezone.utc):
        # Clear expired secrets so they cannot be retried indefinitely.
        user.email_verify_token = None
        user.email_verify_token_expiry = None
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link.")

    user.email_confirmed = True
    user.email_verify_token = None
    user.email_verify_token_expiry = None
    db.commit()
    db.refresh(user)
    return user


def resend_verification_email(db: Session, email: str) -> None:
    """Reissues the verification token for an unverified account.

    The endpoint using this function returns a generic 202 response so callers
    cannot discover whether an email address is registered.
    """
    user = db.query(User).filter(func.lower(User.email) == email.lower()).first()
    if not user or user.email_confirmed or user.is_deleted:
        return

    raw_token, token_hash = _new_email_verification_token()
    user.email_verify_token = token_hash
    user.email_verify_token_expiry = datetime.now(timezone.utc) + timedelta(hours=24)

    try:
        send_verification_email(
            recipient=user.email,
            first_name=user.first_name or user.username,
            token=raw_token,
        )
        db.commit()
    except EmailDeliveryError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email could not be sent right now. Please try again later.",
        ) from exc


class PasswordResetTicket(NamedTuple):
    recipient: str
    first_name: str
    token: str  # raw token - goes into the email only, the DB keeps the hash


def request_password_reset(db: Session, email: str) -> PasswordResetTicket | None:
    """Creates a one-hour reset token and returns what is needed to email it.

    Returns None if the account doesn't exist (no enumeration leak). Only the
    SHA-256 digest of the token is stored, like email-verification tokens.
    """
    user = (
        db.query(User)
        .filter(func.lower(User.email) == email.lower(), User.is_deleted == False)  # noqa: E712
        .first()
    )
    if not user:
        return None
    raw_token = secrets.token_urlsafe(32)
    user.reset_token = _hash_one_time_token(raw_token)
    user.reset_token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    return PasswordResetTicket(user.email, user.first_name or user.username, raw_token)


def deliver_password_reset_email(ticket: PasswordResetTicket) -> None:
    """Runs as a background task so the response time never reveals whether the
    account exists. Failures are logged, never surfaced to the caller."""
    try:
        send_password_reset_email(
            recipient=ticket.recipient, first_name=ticket.first_name, token=ticket.token
        )
    except EmailDeliveryError:
        logger.exception("Password reset email could not be delivered")


def reset_password(db: Session, token: str, new_password: str) -> User:
    user = db.query(User).filter(User.reset_token == _hash_one_time_token(token)).first()
    if not user or not user.reset_token_expiry or user.reset_token_expiry < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token.")
    user.password_hash = hash_password(new_password)
    user.reset_token = None
    user.reset_token_expiry = None
    user.failed_login_attempts = 0
    user.locked_until = None
    # Password reset is a security boundary: revoke every existing refresh
    # token so a previously logged-in session cannot silently mint new access
    # tokens after the password has changed.
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False).update(  # noqa: E712
        {RefreshToken.is_revoked: True}, synchronize_session=False
    )
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    user.password_hash = hash_password(new_password)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False).update(  # noqa: E712
        {RefreshToken.is_revoked: True}, synchronize_session=False
    )
    db.commit()


# --------------------------------------------------------------------- #
# Account deletion — instant or 30-day scheduled
# --------------------------------------------------------------------- #
def schedule_deletion(db: Session, user: User, days: int = 30) -> datetime:
    """Marks the account for deletion in `days` days (default 30) — the existing grace-period path."""
    user.is_deleted = True
    user.deletion_scheduled_at = datetime.now(timezone.utc) + timedelta(days=days)
    db.commit()
    return user.deletion_scheduled_at


def cancel_scheduled_deletion(db: Session, user: User) -> None:
    """Undo a pending scheduled deletion, as long as it hasn't been purged yet."""
    if not user.is_deleted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This account isn't scheduled for deletion.")
    user.is_deleted = False
    user.deletion_scheduled_at = None
    db.commit()


def hard_delete_user(db: Session, user_id: int) -> None:
    """
    Permanently and immediately erases a user and every row they own.

    No FK in this schema uses ON DELETE CASCADE (checked the migration —
    all are plain RESTRICT), so children must be removed in dependency
    order by hand before the user row itself can go. Backend redesign
    added: TransactionDraft/HistoryEntry (new tables), CreditPurchase
    (new, settled via Transaction like Loan), and the three link tables
    (CategoryTransactionType/CategoryGroup/GroupContact) which don't have
    their own user_id column so they're cleaned up via subquery instead:

        transaction_drafts, history_entries (no dependents, go anytime)
        -> transactions (references almost everything below, must go first)
        -> group_contacts / category_groups / category_transaction_types (link rows)
        -> loans, credit_purchases -> budgets -> goals
        -> contacts -> groups -> categories -> payment_methods -> accounts
        -> (their own) transaction_types -> (their own) priorities
        -> refresh_tokens -> user

    System-default transaction_types/priorities (user_id IS NULL) are
    never touched — they're shared across all users.
    """
    from app.models.transaction import Transaction
    from app.models.transaction_draft import TransactionDraft
    from app.models.history_entry import HistoryEntry
    from app.models.loan import Loan
    from app.models.credit_purchase import CreditPurchase
    from app.models.budget import Budget
    from app.models.goal import Goal
    from app.models.contact import Contact
    from app.models.group import Group
    from app.models.category import Category
    from app.models.links import CategoryTransactionType, CategoryGroup, GroupContact
    from app.models.payment_method import PaymentMethod
    from app.models.account import Account
    from app.models.transaction_type import TransactionType
    from app.models.priority import Priority
    from app.models.refresh_token import RefreshToken

    db.query(TransactionDraft).filter(TransactionDraft.user_id == user_id).delete(synchronize_session=False)
    db.query(HistoryEntry).filter(HistoryEntry.user_id == user_id).delete(synchronize_session=False)
    db.query(Transaction).filter(Transaction.user_id == user_id).delete(synchronize_session=False)

    category_ids = select(Category.id).where(Category.user_id == user_id)
    group_ids = select(Group.id).where(Group.user_id == user_id)
    contact_ids = select(Contact.id).where(Contact.user_id == user_id)
    own_type_ids = select(TransactionType.id).where(TransactionType.user_id == user_id)

    db.query(GroupContact).filter(
        GroupContact.group_id.in_(group_ids) | GroupContact.contact_id.in_(contact_ids)
    ).delete(synchronize_session=False)
    db.query(CategoryGroup).filter(
        CategoryGroup.category_id.in_(category_ids) | CategoryGroup.group_id.in_(group_ids)
    ).delete(synchronize_session=False)
    db.query(CategoryTransactionType).filter(
        CategoryTransactionType.category_id.in_(category_ids) | CategoryTransactionType.transaction_type_id.in_(own_type_ids)
    ).delete(synchronize_session=False)

    for model in (
        Loan,
        CreditPurchase,
        Budget,
        Goal,
        Contact,
        Group,
        Category,
        PaymentMethod,
        Account,
        TransactionType,
        Priority,
        RefreshToken,
    ):
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)

    profile_image_url = db.query(User.profile_image_url).filter(User.id == user_id).scalar()
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()

    # Keep file storage tidy when a user is permanently removed (including
    # scheduled-deletion purge jobs, not only the HTTP delete endpoint).
    if profile_image_url:
        from app.utils.profile_images import delete_profile_image

        delete_profile_image(profile_image_url)


def purge_due_deletions(db: Session) -> int:
    """
    Hard-deletes every account whose 30-day scheduled-deletion window has
    passed. There was previously NO mechanism that actually did this —
    `is_deleted`/`deletion_scheduled_at` were set but nothing ever ran to
    finish the job, so "deleted" accounts (and all their data) sat in the
    database forever. Call this from a daily cron job:

        python -m app.utils.purge_deleted_users

    (also invoked once, best-effort, at app startup — see main.py — as a
    safety net for setups that haven't wired up a cron job yet; a real
    cron/scheduled task is the recommended way to run this in production
    since it won't fire again until the process next restarts.)

    Returns the number of accounts purged.
    """
    now = datetime.now(timezone.utc)
    due = db.query(User).filter(User.is_deleted == True, User.deletion_scheduled_at <= now).all()  # noqa: E712
    for user in due:
        hard_delete_user(db, user.id)
    return len(due)
