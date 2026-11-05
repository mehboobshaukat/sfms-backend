from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import group_crud, category_crud, link_group_to_category
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.models.links import CategoryGroup
from app.schemas.group import GroupCreate, GroupUpdate, GroupOut

router = APIRouter(prefix="/groups", tags=["Groups"])


@router.get("", response_model=list[GroupOut])
def list_groups(
    category_slug: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Filter by ?category_slug= to drive the (optional) group narrowing step in the transaction form."""
    groups = group_crud.list(db, current_user.id, active_only=active_only)
    if category_slug:
        category = category_crud.get(db, current_user.id, category_slug)
        groups = [g for g in groups if category.slug in g.category_slugs]
    return groups




@router.get("/paged", response_model=PaginatedResponse[GroupOut])
def paged_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return group_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"category_slugs"})
    group = group_crud.create(db, current_user.id, data)
    for cat_slug in payload.category_slugs:
        category = category_crud.get(db, current_user.id, cat_slug)
        link_group_to_category(db, group.id, category.id)
    db.refresh(group)
    history_service.log_entry(db, current_user.id, "group", group.slug, group.name, action="create", after_snapshot=data)
    db.commit()
    return group


@router.put("/{slug}", response_model=GroupOut)
def update_group(
    slug: str,
    payload: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = group_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name, "description": before.description}
    group = group_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "group", group.slug, group.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return group


@router.post("/{slug}/link/{category_slug}", response_model=GroupOut)
def link_group(
    slug: str,
    category_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = group_crud.get(db, current_user.id, slug)
    category = category_crud.get(db, current_user.id, category_slug)
    link_group_to_category(db, group.id, category.id)
    db.refresh(group)
    return group


@router.delete("/{slug}/link/{category_slug}", response_model=GroupOut)
def unlink_group(
    slug: str,
    category_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = group_crud.get(db, current_user.id, slug)
    category = category_crud.get(db, current_user.id, category_slug)
    db.query(CategoryGroup).filter(CategoryGroup.group_id == group.id, CategoryGroup.category_id == category.id).delete(synchronize_session=False)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently deletes the group everywhere it's linked (orphaning cascade to its contacts). Use the unlink endpoint to remove it from just one category."""
    group = group_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_group_deletion(db, current_user.id, group)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": group.name, "description": group.description}
    label, g_slug = group.name, group.slug
    deletion_guard.delete_group(db, current_user.id, group)
    history_service.log_entry(db, current_user.id, "group", g_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
