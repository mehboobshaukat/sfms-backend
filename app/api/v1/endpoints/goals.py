from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud.base import CRUDBase
from app.crud.lookups import priority_crud
from app.crud import deletion_guard, history_service
from app.models.user import User
from app.models.goal import Goal
from app.schemas.goal import GoalCreate, GoalUpdate, GoalOut, GoalAddSavings, GoalReleaseSavings
from app.crud.goal_savings_service import change_savings, total_reservations, purge_goal_allocations, ledger_snapshot
from app.models.goal_allocation import GoalAllocation

router = APIRouter(prefix="/goals", tags=["Goals"])
goal_crud = CRUDBase(Goal, "goal")


@router.get("", response_model=list[GoalOut])
def list_goals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Goal).options(joinedload(Goal.priority)).filter(Goal.user_id == current_user.id).order_by(Goal.created_at.desc()).all()


@router.get("/progress")
def goals_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard 'goal progress' card: progress % and time-to-target per goal."""
    goals = db.query(Goal).filter(Goal.user_id == current_user.id).all()
    result = []
    for g in goals:
        progress_pct = float(g.saved_amount / g.target_amount * 100) if g.target_amount else 0.0
        days_left = (g.target_date - date.today()).days if g.target_date else None
        result.append(
            {
                "slug": g.slug,
                "name": g.name,
                "progress_percent": round(progress_pct, 2),
                "days_remaining": days_left,
                "is_completed": g.is_completed,
            }
        )
    return result


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
def create_goal(
    payload: GoalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"priority_slug"})
    if payload.priority_slug:
        data["priority_id"] = priority_crud.get(db, current_user.id, payload.priority_slug).id
    try:
        goal = goal_crud.create(db, current_user.id, data, commit=False)
        history_service.log_entry(db, current_user.id, "goal", goal.slug, goal.name,
                                  action="create", after_snapshot=history_service.entity_snapshot(goal, history_service.GOAL_FIELDS))
        db.commit()
        db.refresh(goal)
        return goal
    except Exception:
        db.rollback()
        raise


@router.put("/{slug}", response_model=GoalOut)
def update_goal(
    slug: str,
    payload: GoalUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = goal_crud.get(db, current_user.id, slug)
    before_snapshot = history_service.entity_snapshot(before, history_service.GOAL_FIELDS)
    data = payload.model_dump(exclude_unset=True, exclude={"priority_slug"})
    if "priority_slug" in payload.model_fields_set:
        data["priority_id"] = (
            priority_crud.get(db, current_user.id, payload.priority_slug).id
            if payload.priority_slug else None
        )
    try:
        goal = goal_crud.update(db, current_user.id, slug, data, commit=False, allow_nulls=True)
        if goal.saved_amount >= goal.target_amount:
            goal.is_completed = True
            goal.completed_at = goal.completed_at or date.today()
        else:
            goal.is_completed = False
            goal.completed_at = None
        db.flush()
        history_service.log_entry(db, current_user.id, "goal", goal.slug, goal.name,
                                  action="update", before_snapshot=before_snapshot,
                                  after_snapshot=history_service.entity_snapshot(goal, history_service.GOAL_FIELDS))
        db.commit()
        db.refresh(goal)
        return goal
    except Exception:
        db.rollback()
        raise


@router.get("/savings-summary")
def savings_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.account import Account
    accounts = db.query(Account).filter(Account.user_id == current_user.id, Account.is_system.is_(False)).all()
    actual = sum((a.current_balance for a in accounts), Decimal("0.00"))
    reserved = total_reservations(db, current_user.id)
    return {"goal_savings": reserved, "usable_balance": actual - reserved, "total_financial_funds": actual}


@router.post("/{slug}/add-savings", response_model=GoalOut)
def add_savings(slug: str, payload: GoalAddSavings, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return change_savings(db, current_user.id, slug, payload)


@router.post("/{slug}/release-savings", response_model=GoalOut)
def release_savings(slug: str, payload: GoalReleaseSavings, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return change_savings(db, current_user.id, slug, payload, release=True)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    slug: str,
    confirm: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently deletes the goal. Nothing references a goal, so there's
    no cascade — but if it already has saved progress, the first call
    flags that it'll be lost for good; resubmit with ?confirm=true to proceed.
    """
    goal = goal_crud.get(db, current_user.id, slug)
    plan = deletion_guard.check_goal_deletion(goal)
    if plan.blocked:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    if plan.needs_confirmation and not confirm:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.as_detail())
    before_snapshot = history_service.entity_snapshot(goal, history_service.GOAL_FIELDS)
    try:
        # Recycle-bin model (same as every other entity): the live rows are removed now,
        # History keeps the full snapshot (goal + savings ledger) so Undo can bring it all
        # back. Clearing the entry / 30-day expiry drops the snapshot = permanent delete.
        # Reservations are virtual, so removing them returns the money to usable balance.
        before_snapshot["allocations"] = ledger_snapshot(db, goal)
        purge_goal_allocations(db, goal)
        db.delete(goal)
        history_service.log_entry(db, current_user.id, "goal", goal.slug, goal.name,
                                  action="delete", before_snapshot=before_snapshot)
        db.commit()
    except Exception:
        db.rollback()
        raise
