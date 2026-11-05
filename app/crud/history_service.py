"""
User-facing history: recycle bin (deletions) + undo (any change), backed
by one shared log. See models/history_entry.py for the full design note.

log_entry() is called by other services alongside their own changes
(same db session, NOT committed here — the caller's commit covers both).
This pass wires it into Transaction (the highest-value case — it's
where a mistake, AI-made or not, costs the most) and the "simple" lookup
entities registered in crud/lookups.py (Category, Group, Contact,
TransactionType, Priority, Account, PaymentMethod). Goal and Budget use guarded, snapshot-based undo handlers. Other
entity undo paths remain unchanged.
"""
from datetime import date, datetime, timezone

from fastapi.encoders import jsonable_encoder

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from decimal import Decimal

from app.models.history_entry import HistoryEntry
from app.utils.slugs import generate_slug
from app.crud import lookups, deletion_guard

SIMPLE_ENTITY_CRUD = {
    "category": lookups.category_crud,
    "group": lookups.group_crud,
    "contact": lookups.contact_crud,
    "transaction_type": lookups.transaction_type_crud,
    "priority": lookups.priority_crud,
    "account": lookups.account_crud,
    "payment_method": lookups.payment_method_crud,
}

# entity_type -> delete_fn(db, user_id, obj) — the hard-delete side (used
# to undo a "create") for entities where CRUDBase only offers soft_delete.
SIMPLE_ENTITY_DELETE = {
    "category": lambda db, user_id, obj: deletion_guard.delete_category(db, user_id, obj),
    "group": lambda db, user_id, obj: deletion_guard.delete_group(db, user_id, obj),
    "contact": lambda db, user_id, obj: deletion_guard.delete_contact(db, obj),
    "transaction_type": lambda db, user_id, obj: deletion_guard.delete_transaction_type(db, user_id, obj),
    "priority": lambda db, user_id, obj: deletion_guard.delete_priority(db, user_id, obj),
    "account": lambda db, user_id, obj: deletion_guard.delete_account(db, user_id, obj),
    "payment_method": lambda db, user_id, obj: deletion_guard.delete_payment_method(db, obj),
}


def log_entry(
    db: Session,
    user_id: int,
    entity_type: str,
    entity_slug: str,
    entity_label: str,
    action: str,
    before_snapshot: dict | None = None,
    after_snapshot: dict | None = None,
    performed_by: str = "user",
) -> HistoryEntry:

    # Convert Decimal, date, datetime and other supported
    # Python types into JSON-compatible values.
    def serialize_snapshot(snapshot):
        if snapshot is None:
            return None

        return jsonable_encoder(
            snapshot,
            custom_encoder={
                Decimal: str,
            },
        )

    entry = HistoryEntry(
        user_id=user_id,
        slug=generate_slug(db, HistoryEntry, "history_entry"),
        entity_type=entity_type,
        entity_slug=entity_slug,
        entity_label=entity_label,
        action=action,
        performed_by=performed_by,
        before_snapshot=serialize_snapshot(before_snapshot),
        after_snapshot=serialize_snapshot(after_snapshot),
    )

    db.add(entry)
    db.flush()

    return entry


def list_history(db: Session, user_id: int, entity_type: str | None = None) -> list[HistoryEntry]:
    stmt = select(HistoryEntry).where(HistoryEntry.user_id == user_id, HistoryEntry.expires_at > datetime.now(timezone.utc))
    if entity_type:
        stmt = stmt.where(HistoryEntry.entity_type == entity_type)
    stmt = stmt.order_by(HistoryEntry.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def get_history_entry(db: Session, user_id: int, slug: str) -> HistoryEntry:
    entry = db.execute(select(HistoryEntry).where(HistoryEntry.slug == slug, HistoryEntry.user_id == user_id, HistoryEntry.expires_at > datetime.now(timezone.utc))).scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="History entry not found")
    return entry


def delete_history_entry(db: Session, user_id: int, slug: str) -> None:
    """User manually clears an entry. Only removes the log row — never
    touches the live entity, and forfeits undo/restore for this change."""
    entry = get_history_entry(db, user_id, slug)
    db.delete(entry)
    db.commit()


def purge_expired(db: Session) -> int:
    """Daily cron target, same pattern as purge_deleted_users.py."""
    now = datetime.now(timezone.utc)
    count = db.query(HistoryEntry).filter(HistoryEntry.expires_at <= now).delete(synchronize_session=False)
    db.commit()
    return count


def undo_entry(db: Session, user_id: int, slug: str) -> None:
    entry = get_history_entry(db, user_id, slug)
    if entry.is_undone:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This change was already undone.")

    if entry.entity_type == "transaction":
        _undo_transaction_entry(db, user_id, entry)
    elif entry.entity_type == "credit_purchase":
        _undo_credit_purchase_entry(db, user_id, entry)
    elif entry.entity_type == "goal":
        _undo_goal_entry(db, user_id, entry)
    elif entry.entity_type == "budget":
        _undo_budget_entry(db, user_id, entry)
    elif entry.entity_type in SIMPLE_ENTITY_CRUD:
        _undo_simple_entry(db, user_id, entry)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Undo isn't supported yet for '{entry.entity_type}'.")

    entry.is_undone = True
    entry.undone_at = datetime.now(timezone.utc)
    db.commit()



# Full snapshots are emitted by the Goal/Budget endpoints. Older history
# entries may only contain a partial before_snapshot, or none for deletions.
# Never guess missing fields: refuse unsafe restoration with an explicit 409.
GOAL_FIELDS = (
    "name", "target_amount", "saved_amount", "start_date", "target_date",
    "priority_id", "is_completed", "completed_at",
)
BUDGET_FIELDS = (
    "category_id", "period", "amount", "spent_amount", "start_date",
    "end_date", "is_active",
)


def entity_snapshot(obj, fields: tuple[str, ...]) -> dict:
    """Produce exact, JSON-safe ORM snapshots without float money rounding."""
    return jsonable_encoder(
        {field: getattr(obj, field) for field in fields},
        custom_encoder={Decimal: str},
    )


def _ensure_no_later_history(db: Session, entry: HistoryEntry) -> None:
    """Prevent an old operation from undoing more recent changes."""
    later = db.execute(
        select(HistoryEntry.id).where(
            HistoryEntry.user_id == entry.user_id,
            HistoryEntry.entity_type == entry.entity_type,
            HistoryEntry.entity_slug == entry.entity_slug,
            HistoryEntry.id > entry.id,
            HistoryEntry.is_undone.is_(False),
        ).limit(1)
    ).first()
    if later:
        raise HTTPException(
            409,
            "Newer changes exist for this record. Undo newer changes first.",
        )


def _assert_current_matches(obj, after_snapshot: dict | None, fields: tuple[str, ...]) -> None:
    """Reject stale undo when data changed outside the history workflow."""
    if not after_snapshot:
        return  # legacy history entries did not always store after_snapshot
    current = entity_snapshot(obj, fields)
    for key, value in after_snapshot.items():
        if key not in current:
            continue
        actual = current[key]
        # Old history writes used float/number for amounts, newer writes
        # preserve them as strings. Compare amounts numerically.
        if key in {"target_amount", "saved_amount", "amount", "spent_amount"} and value is not None:
            same = Decimal(str(actual)) == Decimal(str(value))
        else:
            same = actual == value
        if not same:
            raise HTTPException(409, f"Cannot undo: '{key}' changed since this history entry.")


def _require_snapshot(snapshot: dict | None, fields: tuple[str, ...]) -> dict:
    if not snapshot or any(name not in snapshot for name in fields):
        raise HTTPException(409, "This older history entry has no complete snapshot to restore.")
    return snapshot


def _date_value(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _check_goal_priority(db: Session, user_id: int, priority_id: int | None) -> None:
    if priority_id is None:
        return
    from app.models.priority import Priority
    from sqlalchemy import or_
    if not db.query(Priority).filter(
        Priority.id == priority_id,
        or_(Priority.user_id == user_id, Priority.user_id.is_(None)),
    ).first():
        raise HTTPException(409, "The original goal priority no longer exists or is unavailable.")


def _check_budget_category(db: Session, user_id: int, category_id: int) -> None:
    from app.models.category import Category
    from sqlalchemy import or_
    if not db.query(Category).filter(
        Category.id == category_id,
        or_(Category.user_id == user_id, Category.user_id.is_(None)),
    ).first():
        raise HTTPException(409, "The original budget category no longer exists or is unavailable.")


def _apply_goal_fields(db: Session, user_id: int, obj, snapshot: dict) -> None:
    _check_goal_priority(db, user_id, snapshot.get("priority_id", obj.priority_id))
    for key in GOAL_FIELDS:
        if key not in snapshot:
            continue
        value = snapshot[key]
        if key in ("target_amount", "saved_amount"):
            value = Decimal(str(value))
        elif key in ("start_date", "target_date", "completed_at"):
            value = _date_value(value)
        setattr(obj, key, value)
    # Legacy update snapshots did not contain completion fields. Reconcile
    # after restoring target_amount without changing any allocation ledger.
    if "target_amount" in snapshot and "is_completed" not in snapshot:
        obj.is_completed = bool(obj.saved_amount >= obj.target_amount)
        obj.completed_at = (obj.completed_at or date.today()) if obj.is_completed else None


def _apply_budget_fields(obj, snapshot: dict) -> None:
    for key in BUDGET_FIELDS:
        if key not in snapshot:
            continue
        value = snapshot[key]
        if key in ("amount", "spent_amount"):
            value = Decimal(str(value))
        elif key in ("start_date", "end_date"):
            value = _date_value(value)
        setattr(obj, key, value)


def _undo_goal_entry(db: Session, user_id: int, entry: HistoryEntry) -> None:
    from app.models.goal import Goal
    from app.models.goal_allocation import GoalAllocation

    _ensure_no_later_history(db, entry)
    goal = db.query(Goal).filter(Goal.user_id == user_id, Goal.slug == entry.entity_slug).with_for_update().first()
    if entry.action == "create":
        if goal is None:
            raise HTTPException(409, "Goal is already missing; cannot undo creation.")
        if goal.saved_amount != 0:
            raise HTTPException(409, "Goal has saved funds; release them before undoing creation.")
        _assert_current_matches(goal, entry.after_snapshot, GOAL_FIELDS)
        # Fully released goal: its ledger rows net to zero, safe to drop with the goal.
        from app.crud.goal_savings_service import purge_goal_allocations
        purge_goal_allocations(db, goal)
        db.delete(goal)
    elif entry.action == "update":
        if goal is None:
            raise HTTPException(409, "Goal no longer exists.")
        if not entry.before_snapshot:
            raise HTTPException(409, "No prior goal state was recorded.")
        _assert_current_matches(goal, entry.after_snapshot, GOAL_FIELDS)
        _apply_goal_fields(db, user_id, goal, entry.before_snapshot)
    elif entry.action == "delete":
        if goal is not None:
            raise HTTPException(409, "A goal with this identifier already exists.")
        snapshot = _require_snapshot(entry.before_snapshot, ("name", "target_amount", "start_date"))
        obj = Goal(user_id=user_id, slug=entry.entity_slug, name=snapshot["name"],
                   target_amount=Decimal(str(snapshot["target_amount"])),
                   start_date=_date_value(snapshot["start_date"]))
        _apply_goal_fields(db, user_id, obj, snapshot)
        db.add(obj)
        db.flush()
        # Bring back the savings/release transactions + ledger rows kept in the snapshot.
        from app.crud.goal_savings_service import restore_goal_ledger
        restore_goal_ledger(db, user_id, obj, snapshot.get("allocations") or [])
    else:
        raise HTTPException(400, f"Unknown action '{entry.action}'.")
    db.flush()


def _undo_budget_entry(db: Session, user_id: int, entry: HistoryEntry) -> None:
    from app.models.budget import Budget

    _ensure_no_later_history(db, entry)
    budget = db.query(Budget).filter(Budget.user_id == user_id, Budget.slug == entry.entity_slug).with_for_update().first()
    if entry.action == "create":
        if budget is None:
            raise HTTPException(409, "Budget is already missing; cannot undo creation.")
        if budget.spent_amount != 0:
            raise HTTPException(409, "Budget has tracked spending and cannot be removed by Undo.")
        _assert_current_matches(budget, entry.after_snapshot, BUDGET_FIELDS)
        db.delete(budget)
    elif entry.action == "update":
        if budget is None:
            raise HTTPException(409, "Budget no longer exists.")
        if not entry.before_snapshot:
            raise HTTPException(409, "No prior budget state was recorded.")
        _assert_current_matches(budget, entry.after_snapshot, BUDGET_FIELDS)
        if "category_id" in entry.before_snapshot:
            _check_budget_category(db, user_id, entry.before_snapshot["category_id"])
        _apply_budget_fields(budget, entry.before_snapshot)
    elif entry.action == "delete":
        if budget is not None:
            raise HTTPException(409, "A budget with this identifier already exists.")
        snapshot = _require_snapshot(entry.before_snapshot, ("category_id", "period", "amount", "start_date"))
        _check_budget_category(db, user_id, snapshot["category_id"])
        obj = Budget(user_id=user_id, slug=entry.entity_slug,
                     category_id=snapshot["category_id"], period=snapshot["period"],
                     amount=Decimal(str(snapshot["amount"])),
                     start_date=_date_value(snapshot["start_date"]))
        _apply_budget_fields(obj, snapshot)
        db.add(obj)
    else:
        raise HTTPException(400, f"Unknown action '{entry.action}'.")
    db.flush()

def _undo_simple_entry(db: Session, user_id: int, entry: HistoryEntry) -> None:
    crud = SIMPLE_ENTITY_CRUD[entry.entity_type]
    if entry.action == "create":
        obj = crud.get(db, user_id, entry.entity_slug)
        SIMPLE_ENTITY_DELETE[entry.entity_type](db, user_id, obj)
        db.flush()
    elif entry.action == "update":
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded for this change.")
        crud.update(db, user_id, entry.entity_slug, entry.before_snapshot)
    elif entry.action == "delete":
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded to restore.")
        crud.create(db, user_id, entry.before_snapshot)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action '{entry.action}'.")


def _undo_transaction_entry(db: Session, user_id: int, entry: HistoryEntry) -> None:
    from app.crud import transaction_service
    from app.schemas.transaction import TransactionCreate

    if entry.action == "create":
        check = transaction_service.check_transaction_deletion(db, user_id, entry.entity_slug)
        if check["blocked"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=check["message"])
        transaction_service.delete_transaction(db, user_id, entry.entity_slug)
    elif entry.action == "delete":
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded to restore.")
        if "goal_allocation" in entry.before_snapshot:
            # Goal savings/release records are virtual reservations: they have their own
            # restore path (create_transaction() rejects the Goal Saving type on purpose).
            from app.crud.goal_savings_service import restore_goal_transaction
            restore_goal_transaction(db, user_id, entry.before_snapshot)
            return
        # Goes through the SAME validated creation path as any normal
        # transaction — undo never bypasses the business-rule engine.
        transaction_service.create_transaction(db, user_id, TransactionCreate(**entry.before_snapshot))
    elif entry.action == "update":
        # TransactionUpdate only ever touches non-financial fields
        # (description/reference_no/remarks/priority/payment_method), so
        # this is a plain field revert — no balance math involved.
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded for this change.")
        txn = transaction_service.get_transaction_by_code(db, user_id, entry.entity_slug)
        for key, value in entry.before_snapshot.items():
            if hasattr(txn, key):
                setattr(txn, key, value)
        db.flush()
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action '{entry.action}'.")


def _undo_credit_purchase_entry(db: Session, user_id: int, entry: HistoryEntry) -> None:
    """
    CreditPurchase has its own bespoke service (not CRUDBase), so it needs
    its own undo handler rather than going through _undo_simple_entry.
    Deliberately does NOT reach into Transaction/Loan logic — a credit
    purchase's own undo only ever touches the CreditPurchase row itself;
    settling payments are separate Transaction history entries with their
    own undo (via _undo_transaction_entry's credit-purchase-reversal path).
    """
    from app.crud import credit_purchase_service, deletion_guard
    from app.schemas.credit_purchase import CreditPurchaseCreate, CreditPurchaseUpdate

    if entry.action == "create":
        cp = credit_purchase_service.get_credit_purchase(db, user_id, entry.entity_slug)
        plan = deletion_guard.check_credit_purchase_deletion(db, user_id, cp)
        if plan.blocked:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=plan.reason)
        deletion_guard.delete_credit_purchase(db, cp)
        db.flush()
    elif entry.action == "update":
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded for this change.")
        credit_purchase_service.update_credit_purchase(db, user_id, entry.entity_slug, CreditPurchaseUpdate(**entry.before_snapshot))
    elif entry.action == "delete":
        if not entry.before_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No prior state recorded to restore.")
        # Restores the purchase at its ORIGINAL total/remaining amount —
        # any settlement transactions that existed before the delete are
        # NOT replayed (they have their own independent history entries;
        # undo those separately if you need the balance back too).
        credit_purchase_service.create_credit_purchase(db, user_id, CreditPurchaseCreate(**entry.before_snapshot))
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action '{entry.action}'.")
