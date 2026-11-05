from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud import history_service
from app.models.user import User
from app.schemas.history_entry import HistoryEntryOut
from app.models.history_entry import HistoryEntry
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/history", tags=["History"])


@router.get("", response_model=list[HistoryEntryOut])
def list_history(
    entity_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    User-facing activity history — doubles as the recycle bin (filter by
    entity_type and look at action="delete" entries) and the "undo my
    last change" list. Entries auto-expire 30 days after they're created
    unless deleted sooner.
    """
    return history_service.list_history(db, current_user.id, entity_type)


@router.get("/paged", response_model=PaginatedResponse[HistoryEntryOut])
def paged_history(
    entity_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = [HistoryEntry.user_id == current_user.id, HistoryEntry.expires_at > datetime.now(timezone.utc)]
    if entity_type:
        filters.append(HistoryEntry.entity_type == entity_type)
    total = db.scalar(select(func.count()).select_from(HistoryEntry).where(*filters)) or 0
    rows = db.execute(
        select(HistoryEntry).where(*filters)
        .order_by(HistoryEntry.created_at.desc(), HistoryEntry.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/{slug}/undo", status_code=200)
def undo_entry(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Undoes a create (deletes it), an update (reverts the fields), or a delete (restores it)."""
    history_service.undo_entry(db, current_user.id, slug)
    return {"detail": "Undone."}


@router.delete("/{slug}", status_code=204)
def delete_history_entry(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User manually clears this entry. Only removes the log row — never touches the live entity — and forfeits undo/restore for this change."""
    history_service.delete_history_entry(db, current_user.id, slug)
