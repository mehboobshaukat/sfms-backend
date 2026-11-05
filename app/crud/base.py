"""
Generic CRUD helpers for simple, user-scoped "lookup" entities
(TransactionType, Category, Group, Contact, Account, PaymentMethod, Priority).

Every query is scoped by user_id (rule 7: "no cross-user data leakage").
For entities that support a system-default row (user_id IS NULL), pass
`include_system_default=True` so GET/list also returns those rows.

Backend redesign: entities are now looked up by their public `slug`
(never the raw integer `id`, which stays internal-only — see
app/utils/slugs.py), and entities listed in NAME_UNIQUE_ENTITIES get a
case/whitespace-insensitive unique-name check on create and rename.
"""
from typing import TypeVar, Generic, Type, Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select, func
from sqlalchemy.orm import Session, selectinload

from app.utils.slugs import generate_slug, assert_unique_name, NAME_UNIQUE_ENTITIES

ModelType = TypeVar("ModelType", bound=Any)


class CRUDBase(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], entity_key: str, include_system_default: bool = False):
        self.model = model
        self.entity_key = entity_key  # key into app.utils.slugs.ENTITY_PREFIXES / NAME_UNIQUE_ENTITIES
        self.include_system_default = include_system_default

    def _user_scope_statement(self, user_id: int):
        stmt = select(self.model)
        if self.include_system_default:
            stmt = stmt.where(or_(self.model.user_id == user_id, self.model.user_id.is_(None)))
        else:
            stmt = stmt.where(self.model.user_id == user_id)
        return stmt

    def get(self, db: Session, user_id: int, slug: str) -> ModelType:
        stmt = self._user_scope_statement(user_id).where(self.model.slug == slug)
        obj = db.execute(stmt).scalar_one_or_none()
        if not obj:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{self.model.__name__} not found")
        return obj


    def _list_options(self):
        """Batch relationship-backed public slugs; avoids O(n) lazy SELECTs.

        These are deliberately restricted to the relationships serialized by
        settings list schemas. selectinload emits bounded batch queries.
        """
        if self.entity_key == "category":
            from app.models.category import Category
            from app.models.links import CategoryTransactionType, CategoryGroup
            return (
                selectinload(Category.transaction_type_links).selectinload(CategoryTransactionType.transaction_type),
                selectinload(Category.group_links).selectinload(CategoryGroup.group),
            )
        if self.entity_key == "group":
            from app.models.group import Group
            from app.models.links import CategoryGroup, GroupContact
            return (
                selectinload(Group.category_links).selectinload(CategoryGroup.category),
                selectinload(Group.contact_links).selectinload(GroupContact.contact),
            )
        if self.entity_key == "contact":
            from app.models.contact import Contact
            from app.models.links import GroupContact
            return (selectinload(Contact.group_links).selectinload(GroupContact.group),)
        if self.entity_key == "payment_method":
            from app.models.payment_method import PaymentMethod
            return (selectinload(PaymentMethod.account),)
        return ()

    def _filtered_list_statement(self, user_id: int, active_only: bool = False, **filters):
        stmt = self._user_scope_statement(user_id)
        if active_only and hasattr(self.model, "is_active"):
            stmt = stmt.where(self.model.is_active.is_(True))
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        return stmt

    def paginate(self, db: Session, user_id: int, page: int = 1, page_size: int = 20,
                 active_only: bool = False, **filters) -> dict:
        """Server-side pagination. Never fetch all rows before slicing.

        FastAPI validates these parameters at the API layer, but guard this
        reusable helper too in case it is called elsewhere.
        """
        if page < 1 or page_size < 1 or page_size > 200:
            raise HTTPException(status_code=422, detail="page must be >= 1 and page_size between 1 and 200")
        stmt = self._filtered_list_statement(user_id, active_only, **filters)
        total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        stmt = (stmt.options(*self._list_options())
                    .order_by(self.model.created_at.desc(), self.model.id.desc())
                    .offset((page - 1) * page_size).limit(page_size))
        items = list(db.execute(stmt).scalars().all())
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    def list(self, db: Session, user_id: int, active_only: bool = False, **filters) -> list[ModelType]:
        # Keep the original array contract for dropdowns and other callers.
        # The UI's large settings tables instead call /paged.
        stmt = self._filtered_list_statement(user_id, active_only, **filters)
        stmt = stmt.options(*self._list_options()).order_by(self.model.created_at.desc(), self.model.id.desc())
        return list(db.execute(stmt).scalars().all())

    def create(self, db: Session, user_id: int, obj_in: dict, *, commit: bool = True) -> ModelType:
        if self.entity_key in NAME_UNIQUE_ENTITIES and obj_in.get("name"):
            assert_unique_name(db, self.model, user_id, obj_in["name"], self.entity_key)
        # Slug must exist BEFORE the row is written (column is NOT NULL);
        # generate_slug only needs the session to check for collisions,
        # not the new object's id, so no intermediate flush is needed.
        slug = generate_slug(db, self.model, self.entity_key)
        obj = self.model(user_id=user_id, slug=slug, **obj_in)
        db.add(obj)
        db.flush()
        if commit:
            db.commit()
        db.refresh(obj)
        return obj

    def update(self, db: Session, user_id: int, slug: str, obj_in: dict, *, commit: bool = True, allow_nulls: bool = False) -> ModelType:
        obj = self.get(db, user_id, slug)
        # system-default rows (user_id NULL) can't be edited by a normal user
        if obj.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System default records cannot be modified. Create your own custom entry instead.",
            )
        new_name = obj_in.get("name")
        if self.entity_key in NAME_UNIQUE_ENTITIES and new_name and new_name != obj.name:
            assert_unique_name(db, self.model, user_id, new_name, self.entity_key, exclude_id=obj.id)
        for key, value in obj_in.items():
            if value is not None or allow_nulls:
                setattr(obj, key, value)
        db.flush()
        if commit:
            db.commit()
        db.refresh(obj)
        return obj

    def soft_delete(self, db: Session, user_id: int, slug: str) -> ModelType:
        """"Deactivate" (is_active=False) — distinct from the real hard-delete
        path in app/crud/deletion_guard.py. Kept for the lighter-weight
        "hide without checking dependents" action."""
        obj = self.get(db, user_id, slug)
        if obj.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System default records cannot be deleted.",
            )
        if hasattr(obj, "is_active"):
            obj.is_active = False
        db.commit()
        db.refresh(obj)
        return obj
