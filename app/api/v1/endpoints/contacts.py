from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.schemas.common import PaginatedResponse

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.lookups import contact_crud, group_crud, category_crud, link_contact_to_group, contacts_for_category
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.models.links import GroupContact
from app.schemas.contact import ContactCreate, ContactUpdate, ContactOut

router = APIRouter(prefix="/contacts", tags=["Contacts"])


@router.get("", response_model=list[ContactOut])
def list_contacts(
    category_slug: str | None = None,
    group_slug: str | None = None,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Powers the transaction form's auto-loaded contact list. Pass
    ?category_slug= alone to get every contact reachable from that
    category (Group is optional — union across all its linked groups),
    or add &group_slug= to narrow to one specific group.
    """
    if category_slug:
        category = category_crud.get(db, current_user.id, category_slug)
        group_id = None
        if group_slug:
            group_id = group_crud.get(db, current_user.id, group_slug).id
        return contacts_for_category(db, current_user.id, category.id, group_id)
    return contact_crud.list(db, current_user.id, active_only=active_only)




@router.get("/paged", response_model=PaginatedResponse[ContactOut])
def paged_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Paginated Settings table. Original GET / remains an array for dropdowns."""
    return contact_crud.paginate(db, current_user.id, page=page, page_size=page_size, active_only=active_only)


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: ContactCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"group_slugs"})
    contact = contact_crud.create(db, current_user.id, data)
    for grp_slug in payload.group_slugs:
        group = group_crud.get(db, current_user.id, grp_slug)
        link_contact_to_group(db, contact.id, group.id)
    db.refresh(contact)
    history_service.log_entry(db, current_user.id, "contact", contact.slug, contact.name, action="create", after_snapshot=data)
    db.commit()
    return contact


@router.put("/{slug}", response_model=ContactOut)
def update_contact(
    slug: str,
    payload: ContactUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = contact_crud.get(db, current_user.id, slug)
    before_snapshot = {"name": before.name, "phone": before.phone, "email": before.email, "address": before.address, "notes": before.notes}
    contact = contact_crud.update(db, current_user.id, slug, payload.model_dump(exclude_unset=True))
    history_service.log_entry(db, current_user.id, "contact", contact.slug, contact.name, action="update", before_snapshot=before_snapshot)
    db.commit()
    return contact


@router.post("/{slug}/link/{group_slug}", response_model=ContactOut)
def link_contact(
    slug: str,
    group_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contact = contact_crud.get(db, current_user.id, slug)
    group = group_crud.get(db, current_user.id, group_slug)
    link_contact_to_group(db, contact.id, group.id)
    db.refresh(contact)
    return contact


@router.delete("/{slug}/link/{group_slug}", response_model=ContactOut)
def unlink_contact(
    slug: str,
    group_slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contact = contact_crud.get(db, current_user.id, slug)
    group = group_crud.get(db, current_user.id, group_slug)
    db.query(GroupContact).filter(GroupContact.contact_id == contact.id, GroupContact.group_id == group.id).delete(synchronize_session=False)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    slug: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently deletes the contact. Blocked if referenced by any loan, credit purchase, or active transaction."""
    contact = contact_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_contact_deletion(db, current_user.id, contact)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = {"name": contact.name, "phone": contact.phone, "email": contact.email, "address": contact.address, "notes": contact.notes}
    label, c_slug = contact.name, contact.slug
    deletion_guard.delete_contact(db, contact)
    history_service.log_entry(db, current_user.id, "contact", c_slug, label, action="delete", before_snapshot=before_snapshot)
    db.commit()
