from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.limiter import limiter
from app.crud import contact_message_service as svc
from app.models.user import User
from app.schemas.admin import (
    MessageBulk, MessageDetailOut, MessageStatsOut, MessageStatus, MessageSummaryOut,
    MessageUpdate, MessageView, ReplyCreate,
)
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/messages", tags=["Admin - Contact messages"])


# NOTE: fixed paths (/stats, /bulk) are declared before /{slug} on purpose.
@router.get("", response_model=PaginatedResponse[MessageSummaryOut])
def list_messages(
    view: MessageView = "inbox",
    status_filter: MessageStatus | None = Query(None, alias="status"),
    is_read: bool | None = None,
    q: str | None = Query(None, max_length=100),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total, items = svc.list_messages(
        db, view=view, status_filter=status_filter, is_read=is_read, q=q,
        date_from=date_from, date_to=date_to, page=page, page_size=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/stats", response_model=MessageStatsOut)
def message_stats(db: Session = Depends(get_db)):
    return svc.stats(db)


@router.post("/bulk")
def bulk_messages(
    request: Request, payload: MessageBulk,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    updated = svc.bulk_action(db, admin, payload.slugs, payload.action, get_remote_address(request))
    return {"updated": updated}


@router.get("/{slug}", response_model=MessageDetailOut)
def get_message(slug: str, db: Session = Depends(get_db)):
    """Read-only: opening a message does not mark it read (the panel calls PATCH for that)."""
    return svc.detail_dict(svc.get_or_404(db, slug))


@router.patch("/{slug}", response_model=MessageDetailOut)
def update_message(slug: str, payload: MessageUpdate, db: Session = Depends(get_db)):
    msg = svc.update_message(db, svc.get_or_404(db, slug), payload)
    return svc.detail_dict(msg)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def trash_message(
    slug: str, request: Request,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Moves the message to the trash. It is purged for good after 30 days."""
    svc.trash_message(db, admin, svc.get_or_404(db, slug), get_remote_address(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{slug}/restore", response_model=MessageDetailOut)
def restore_message(slug: str, db: Session = Depends(get_db)):
    return svc.detail_dict(svc.restore_message(db, svc.get_or_404(db, slug)))


@router.post("/{slug}/replies", response_model=MessageDetailOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/hour")
def reply_to_message(
    request: Request, slug: str, payload: ReplyCreate,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Emails the reply to the person who wrote in and keeps it in the message thread."""
    msg = svc.add_reply(db, admin, svc.get_or_404(db, slug), payload, get_remote_address(request))
    return svc.detail_dict(msg)
