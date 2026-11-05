"""Contact-form inbox: public submissions + admin handling (read/archive/trash/reply)."""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import EmailDeliveryError, send_contact_reply_email
from app.crud import admin_audit
from app.models.contact_message import ContactMessage, ContactMessageReply
from app.models.user import User
from app.schemas.admin import ContactSubmit, MessageUpdate, ReplyCreate
from app.utils.slugs import generate_slug

TRASH_RETENTION_DAYS = 30
MAX_PER_IP_PER_DAY = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_ip(ip: str | None) -> str | None:
    """HMAC of the IP with the server secret: usable for abuse limits, useless as personal data."""
    if not ip:
        return None
    return hmac.new(settings.JWT_SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- public side
def create_submission(db: Session, data: ContactSubmit, ip: str | None) -> bool:
    """Stores a submission. Returns False when it was silently dropped (duplicate)."""
    ip_hash = hash_ip(ip)
    since_day = _now() - timedelta(days=1)
    if ip_hash:
        sent_today = db.scalar(
            select(func.count()).select_from(ContactMessage)
            .where(ContactMessage.ip_hash == ip_hash, ContactMessage.created_at >= since_day)
        ) or 0
        if sent_today >= MAX_PER_IP_PER_DAY:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages sent today. Please try again tomorrow.")

    duplicate = db.scalar(
        select(ContactMessage.id).where(
            func.lower(ContactMessage.email) == data.email.lower(),
            ContactMessage.subject == data.subject,
            ContactMessage.message == data.message,
            ContactMessage.created_at >= _now() - timedelta(hours=1),
        ).limit(1)
    )
    if duplicate is not None:
        return False

    db.add(ContactMessage(
        slug=generate_slug(db, ContactMessage, "contact_message"),
        name=data.name, email=data.email, subject=data.subject, message=data.message, ip_hash=ip_hash,
    ))
    db.commit()
    return True


# ----------------------------------------------------------------- admin side
def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= 140 else flat[:137] + "..."


def summary_dict(msg: ContactMessage, reply_count: int) -> dict:
    return {
        "slug": msg.slug, "name": msg.name, "email": msg.email, "subject": msg.subject,
        "preview": _preview(msg.message), "status": msg.status, "is_read": msg.is_read,
        "is_archived": msg.is_archived, "is_trashed": msg.deleted_at is not None,
        "reply_count": reply_count, "created_at": msg.created_at,
    }


def detail_dict(msg: ContactMessage) -> dict:
    return {
        "slug": msg.slug, "name": msg.name, "email": msg.email, "subject": msg.subject,
        "message": msg.message, "status": msg.status, "is_read": msg.is_read,
        "is_archived": msg.is_archived, "is_trashed": msg.deleted_at is not None,
        "deleted_at": msg.deleted_at, "created_at": msg.created_at,
        "replies": [
            {"admin_name": r.admin_name, "subject": r.subject, "body": r.body, "created_at": r.created_at}
            for r in msg.replies
        ],
    }


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_messages(
    db: Session, *, view: str, status_filter: str | None, is_read: bool | None, q: str | None,
    date_from: datetime | None, date_to: datetime | None, page: int, page_size: int,
) -> tuple[int, list[dict]]:
    M, R = ContactMessage, ContactMessageReply
    filters = []
    if view == "inbox":
        filters += [M.deleted_at.is_(None), M.is_archived.is_(False)]
    elif view == "archived":
        filters += [M.deleted_at.is_(None), M.is_archived.is_(True)]
    elif view == "trash":
        filters.append(M.deleted_at.is_not(None))
    else:  # all (everything except trash)
        filters.append(M.deleted_at.is_(None))
    if status_filter:
        filters.append(M.status == status_filter)
    if is_read is not None:
        filters.append(M.is_read.is_(is_read))
    if q:
        like = f"%{_escape_like(q.strip()[:100])}%"
        filters.append(or_(
            M.name.ilike(like, escape="\\"), M.email.ilike(like, escape="\\"),
            M.subject.ilike(like, escape="\\"), M.message.ilike(like, escape="\\"),
        ))
    if date_from:
        filters.append(M.created_at >= date_from)
    if date_to:
        filters.append(M.created_at <= date_to)

    total = db.scalar(select(func.count()).select_from(M).where(*filters)) or 0
    reply_count = select(func.count(R.id)).where(R.message_id == M.id).correlate(M).scalar_subquery()
    rows = db.execute(
        select(M, reply_count.label("reply_count")).where(*filters)
        .order_by(M.created_at.desc(), M.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return total, [summary_dict(m, int(c or 0)) for m, c in rows]


def stats(db: Session) -> dict:
    M = ContactMessage
    live = M.deleted_at.is_(None)

    def count_if(*conds):
        return func.coalesce(func.sum(case((and_(*conds), 1), else_=0)), 0)

    row = db.execute(select(
        count_if(live, M.is_archived.is_(False), M.is_read.is_(False)),
        count_if(live, M.is_archived.is_(False), M.status == "new"),
        count_if(live, M.is_archived.is_(False), M.status == "in_progress"),
        count_if(live, M.is_archived.is_(False), M.status == "resolved"),
        count_if(live, M.is_archived.is_(True)),
        count_if(M.deleted_at.is_not(None)),
    )).one()
    keys = ("unread", "new", "in_progress", "resolved", "archived", "trash")
    return {k: int(v or 0) for k, v in zip(keys, row)}


def get_or_404(db: Session, slug: str) -> ContactMessage:
    msg = db.scalar(select(ContactMessage).where(ContactMessage.slug == slug))
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found.")
    return msg


def _assert_not_trashed(msg: ContactMessage) -> None:
    if msg.deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This message is in the trash. Restore it first.")


def update_message(db: Session, msg: ContactMessage, patch: MessageUpdate) -> ContactMessage:
    _assert_not_trashed(msg)
    if patch.is_read is not None:
        msg.is_read = patch.is_read
    if patch.status is not None:
        msg.status = patch.status
    if patch.is_archived is not None:
        msg.is_archived = patch.is_archived
    db.commit()
    db.refresh(msg)
    return msg


def trash_message(db: Session, actor: User, msg: ContactMessage, ip: str | None) -> None:
    if msg.deleted_at is None:
        msg.deleted_at = _now()
        admin_audit.record(db, actor, "message.delete", "contact_message", msg.slug, ip=ip)
    db.commit()


def restore_message(db: Session, msg: ContactMessage) -> ContactMessage:
    msg.deleted_at = None
    db.commit()
    db.refresh(msg)
    return msg


def bulk_action(db: Session, actor: User, slugs: list[str], action: str, ip: str | None) -> int:
    messages = db.scalars(select(ContactMessage).where(ContactMessage.slug.in_(set(slugs)))).all()
    changed = 0
    for msg in messages:
        trashed = msg.deleted_at is not None
        if action == "restore":
            if trashed:
                msg.deleted_at = None
                changed += 1
        elif action == "delete":
            if not trashed:
                msg.deleted_at = _now()
                changed += 1
        elif not trashed:  # read / unread / archive / unarchive never touch trashed rows
            if action == "read":
                msg.is_read = True
            elif action == "unread":
                msg.is_read = False
            elif action == "archive":
                msg.is_archived = True
            elif action == "unarchive":
                msg.is_archived = False
            changed += 1
    if action == "delete" and changed:
        admin_audit.record(db, actor, "message.bulk_delete", "contact_message", "bulk", detail=f"{changed} message(s)", ip=ip)
    db.commit()
    return changed


def add_reply(db: Session, admin: User, msg: ContactMessage, payload: ReplyCreate, ip: str | None) -> ContactMessage:
    """Emails the reply FIRST; the reply row is only kept if delivery succeeded."""
    _assert_not_trashed(msg)
    admin_name = " ".join(filter(None, [admin.first_name, admin.last_name])) or admin.username
    subject = payload.subject or (msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}")
    subject = subject[:200]

    reply = ContactMessageReply(message_id=msg.id, admin_id=admin.id, admin_name=admin_name[:160],
                                subject=subject, body=payload.body)
    db.add(reply)
    db.flush()
    try:
        send_contact_reply_email(
            recipient=msg.email, recipient_name=msg.name, subject=subject, body=payload.body,
            admin_name=admin_name, original_message=msg.message,
            original_date=msg.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        )
    except EmailDeliveryError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The reply could not be emailed right now. Please try again later.") from exc

    msg.is_read = True
    if payload.mark_resolved:
        msg.status = "resolved"
    elif msg.status == "new":
        msg.status = "in_progress"
    admin_audit.record(db, admin, "message.reply", "contact_message", msg.slug, detail=subject, ip=ip)
    db.commit()
    db.refresh(msg)
    return msg


def purge_trashed(db: Session) -> int:
    """Permanently removes messages trashed more than TRASH_RETENTION_DAYS ago
    (replies go with them via the FK's ON DELETE CASCADE)."""
    cutoff = _now() - timedelta(days=TRASH_RETENTION_DAYS)
    count = db.query(ContactMessage).filter(
        ContactMessage.deleted_at.is_not(None), ContactMessage.deleted_at < cutoff
    ).delete(synchronize_session=False)
    db.commit()
    return count
