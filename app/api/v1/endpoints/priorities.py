from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import priority_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.schemas.priority import PriorityCreate, PriorityUpdate, PriorityOut

router = APIRouter(prefix="/priorities", tags=["Priorities"])


@router.get("", response_model=list[PriorityOut])
def list_priorities(
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the user's custom priorities PLUS the 3 system defaults (Emergency/Necessary/Wish)."""
    return priority_crud.list(db, current_user.id, active_only=active_only)




@router.get("/paged", response_model=PaginatedResponse[PriorityOut])
def paged_priorities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return priority_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=PriorityOut, status_code=status.HTTP_201_CREATED)
def create_priority(
    payload: PriorityCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump()
    priority = priority_crud.create(db, current_user.id, data)
    history_service.log_entry(db, current_user.id, "priority", priority.slug, priority.name, action="create", after_snapshot=data)
    db.commit()
    return priority


@router.put("/{slug}", response_model=PriorityOut)
def update_priority(
    slug: str,
    payload: PriorityUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = priority_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name}
    priority = priority_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "priority", priority.slug, priority.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return priority


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_priority(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the priority. Blocked if any transaction still
    uses it — that FK is mandatory. If only goals reference it, the first
    call returns a 409 explaining those goals will have their priority
    cleared (not deleted) — resubmit with ?confirm=true to proceed.
    """
    priority = priority_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_priority_deletion(db, current_user.id, priority)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": priority.name}
    label, p_slug = priority.name, priority.slug
    deletion_guard.delete_priority(db, current_user.id, priority)
    history_service.log_entry(db, current_user.id, "priority", p_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
