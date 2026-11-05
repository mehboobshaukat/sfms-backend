from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.base import CRUDBase
from app.crud.lookups import category_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetUpdate, BudgetOut

router = APIRouter(prefix="/budgets", tags=["Budgets"])
budget_crud = CRUDBase(Budget, "budget")


@router.get("", response_model=list[BudgetOut])
def list_budgets(
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Budget).options(joinedload(Budget.category)).filter(Budget.user_id == current_user.id)
    if active_only:
        query = query.filter(Budget.is_active.is_(True))
    return query.order_by(Budget.created_at.desc()).all()


@router.get("/current", response_model=list[BudgetOut])
def current_budgets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Budgets whose date range covers today (dashboard 'budget status' card)."""
    today = date.today()
    return (
        db.query(Budget)
        .filter(
            Budget.user_id == current_user.id,
            Budget.is_active == True,  # noqa: E712
            Budget.start_date <= today,
        )
        .filter((Budget.end_date.is_(None)) | (Budget.end_date >= today))
        .all()
    )


@router.post("", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
def create_budget(
    payload: BudgetCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = category_crud.get(db, current_user.id, payload.category_slug)
    data = payload.model_dump(exclude={"category_slug"})
    data["category_id"] = category.id
    try:
        budget = budget_crud.create(db, current_user.id, data, commit=False)
        history_service.log_entry(db, current_user.id, "budget", budget.slug,
                                  f"{category.name} ({budget.period})", action="create",
                                  after_snapshot=history_service.entity_snapshot(budget, history_service.BUDGET_FIELDS))
        db.commit()
        db.refresh(budget)
        return budget
    except Exception:
        db.rollback()
        raise


@router.put("/{slug}", response_model=BudgetOut)
def update_budget(
    slug: str,
    payload: BudgetUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = budget_crud.get(db, current_user.id, slug)
    before_snapshot = history_service.entity_snapshot(before, history_service.BUDGET_FIELDS)
    data = payload.model_dump(exclude_unset=True)
    try:
        budget = budget_crud.update(db, current_user.id, slug, data,
                                    commit=False, allow_nulls=True)
        history_service.log_entry(db, current_user.id, "budget", budget.slug,
                                  f"budget ({budget.period})", action="update",
                                  before_snapshot=before_snapshot,
                                  after_snapshot=history_service.entity_snapshot(budget, history_service.BUDGET_FIELDS))
        db.commit()
        db.refresh(budget)
        return budget
    except Exception:
        db.rollback()
        raise


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the budget. Nothing references a budget, so
    there's no cascade — but if it already has tracked spending, the
    first call flags that it'll be lost for good; resubmit with
    ?confirm=true to proceed.
    """
    budget = budget_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_budget_deletion(budget)
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = history_service.entity_snapshot(budget, history_service.BUDGET_FIELDS)
    try:
        db.delete(budget)
        history_service.log_entry(db, current_user.id, "budget", budget.slug,
                                  f"budget ({budget.period})", action="delete",
                                  before_snapshot=before_snapshot)
        db.commit()
    except Exception:
        db.rollback()
        raise
