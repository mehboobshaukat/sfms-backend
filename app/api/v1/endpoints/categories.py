from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import category_crud, transaction_type_crud, link_category_to_type
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.models.links import CategoryTransactionType
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryOut

router = APIRouter(prefix="/categories", tags=["Categories"])


def _label(category) -> str:
    return category.name


@router.get("", response_model=list[CategoryOut])
def list_categories(
    transaction_type_slug: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Filter by ?transaction_type_slug= to drive the category dropdown for a given type."""
    categories = category_crud.list(db, current_user.id, active_only=active_only)
    if transaction_type_slug:
        txn_type = transaction_type_crud.get(db, current_user.id, transaction_type_slug)
        categories = [c for c in categories if txn_type.slug in c.transaction_type_slugs]
    return categories




@router.get("/paged", response_model=PaginatedResponse[CategoryOut])
def paged_categories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return category_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    transaction_type_slugs links the category to one or more types right
    away — pass the slug of a category you already created under a
    different type to reuse it here instead of creating a duplicate.
    """
    data = payload.model_dump(exclude={"transaction_type_slugs"})
    category = category_crud.create(db, current_user.id, data)
    for tt_slug in payload.transaction_type_slugs:
        txn_type = transaction_type_crud.get(db, current_user.id, tt_slug)
        link_category_to_type(db, category.id, txn_type.id)
    db.refresh(category)
    history_service.log_entry(db, current_user.id, "category", category.slug, _label(category), action="create", after_snapshot=data)
    db.commit()
    return category


@router.put("/{slug}", response_model=CategoryOut)
def update_category(
    slug: str,
    payload: CategoryUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = category_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name, "description": before.description, "icon": before.icon, "color": before.color}
    category = category_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "category", category.slug, _label(category), action="update", before_snapshot=before_snapshot)
    db.commit()
    return category


@router.post("/{slug}/link/{transaction_type_slug}", response_model=CategoryOut)
def link_category(
    slug: str,
    transaction_type_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Links this (already-existing) category to another transaction type — the core Option-B reuse action."""
    category = category_crud.get(db, current_user.id, slug)
    txn_type = transaction_type_crud.get(db, current_user.id, transaction_type_slug)
    link_category_to_type(db, category.id, txn_type.id)
    db.refresh(category)
    return category


@router.delete("/{slug}/link/{transaction_type_slug}", response_model=CategoryOut)
def unlink_category(
    slug: str,
    transaction_type_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Removes this category from one transaction type only — the category itself, and its other links, are untouched."""
    category = category_crud.get(db, current_user.id, slug)
    txn_type = transaction_type_crud.get(db, current_user.id, transaction_type_slug)
    db.query(CategoryTransactionType).filter(
        CategoryTransactionType.category_id == category.id, CategoryTransactionType.transaction_type_id == txn_type.id
    ).delete(synchronize_session=False)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the category EVERYWHERE it's linked. Blocked if
    any transaction still uses it. If deleting it would orphan groups/
    contacts (or it has budgets), the first call returns a 409 cascade
    summary — resubmit with ?confirm=true to proceed. To remove it from
    just ONE transaction type while keeping it elsewhere, use the
    unlink endpoint instead.
    """
    category = category_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_category_deletion(db, current_user.id, category)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": category.name, "description": category.description, "icon": category.icon, "color": category.color}
    # NOTE: transaction_type_slugs deliberately excluded from the restore
    # snapshot — undoing this delete recreates the category but its type
    # links must be re-added manually (via the link endpoint above).
    label = _label(category)
    cat_slug = category.slug
    deletion_guard.delete_category(db, current_user.id, category)
    history_service.log_entry(db, current_user.id, "category", cat_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
