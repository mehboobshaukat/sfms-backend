"""Atomic goal allocation/release tied to the existing Transactions table."""
from datetime import date
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.goal import Goal
from app.models.goal_allocation import GoalAllocation
from app.models.account import Account
from app.models.category import Category
from app.models.transaction_type import TransactionType
from app.models.transaction import Transaction
from app.models.priority import Priority
from app.models.links import CategoryTransactionType
from app.crud import lookups
from app.utils.codes import generate_transaction_code
from app.utils.slugs import generate_slug


def total_reservations(db: Session, user_id: int, account_id: int | None = None, goal_id: int | None = None):
    query = db.query(func.coalesce(func.sum(GoalAllocation.amount), 0)).filter(GoalAllocation.user_id == user_id)
    if account_id is not None: query = query.filter(GoalAllocation.account_id == account_id)
    if goal_id is not None: query = query.filter(GoalAllocation.goal_id == goal_id)
    return Decimal(query.scalar() or 0)


def _defaults(db, uid):
    txn_type = db.query(TransactionType).filter(TransactionType.name == "Goal Saving", TransactionType.user_id.is_(None)).first()
    if not txn_type: raise HTTPException(500, "Run the latest Alembic migration to enable Goal Saving.")
    cat = db.query(Category).filter(Category.user_id == uid, Category.name == "Goal Savings").first()
    if not cat:
        cat = Category(user_id=uid, name="Goal Savings", slug=generate_slug(db, Category, "category"), is_active=True)
        db.add(cat); db.flush()
    link = db.query(CategoryTransactionType).filter_by(category_id=cat.id, transaction_type_id=txn_type.id).first()
    if not link: db.add(CategoryTransactionType(category_id=cat.id, transaction_type_id=txn_type.id))
    priority = db.query(Priority).filter(Priority.name == "Necessary", Priority.user_id.is_(None)).first()
    if priority is None: priority = db.query(Priority).filter(Priority.user_id == uid).first()
    if priority is None: raise HTTPException(400, "Configure a priority before adding savings.")
    return txn_type,cat,priority


def change_savings(db: Session, uid: int, slug: str, payload, release=False):
    # Serializes all reservation mutations for a user, including two goals sharing one account.
    # Caller owns a fresh request transaction; commit only after transaction + ledger + goal update.
    from app.models.user import User
    db.query(User).filter(User.id == uid).with_for_update().one()
    existing = db.query(GoalAllocation).filter_by(user_id=uid, request_key=payload.request_key).first()
    if existing:
        if (existing.goal.slug != slug or abs(existing.amount) != payload.amount or (existing.amount < 0) != release):
            raise HTTPException(409, "This request key was already used for a different action.")
        return existing.goal
    goal = db.query(Goal).filter(Goal.user_id == uid, Goal.slug == slug).with_for_update().first()
    if not goal: raise HTTPException(404, "Goal not found.")
    if not release and goal.is_completed: raise HTTPException(400, "Completed goals cannot receive new savings.")
    account = db.query(Account).filter(Account.user_id == uid, Account.slug == payload.account_slug, Account.is_active.is_(True), Account.is_system.is_(False)).with_for_update().first()
    if not account: raise HTTPException(400, "Select an active account you own.")
    method = lookups.payment_method_crud.get(db, uid, payload.payment_method_slug)
    lookups.assert_payment_method_matches_account(db, uid, method.id, account.id)
    if not method.is_active:
        raise HTTPException(400, "Payment method is inactive.")
    held = total_reservations(db, uid, account.id)
    held_for_goal = db.query(func.coalesce(func.sum(GoalAllocation.amount), 0)).filter_by(user_id=uid, goal_id=goal.id, account_id=account.id).scalar()
    if release:
        if payload.amount > Decimal(held_for_goal or 0): raise HTTPException(400, "Release exceeds savings reserved for this goal in this account.")
    elif payload.amount > account.current_balance - held:
        raise HTTPException(400, "Not enough unreserved balance in this account.")
    kind,cat,priority = _defaults(db, uid)
    transaction = Transaction(
        user_id=uid, transaction_code=generate_transaction_code(db, uid, payload.txn_date),
        txn_date=payload.txn_date, txn_time=payload.txn_time,
        transaction_type_id=kind.id, category_id=cat.id,
        description=payload.description, from_account_id=account.id,
        payment_method_id=method.id, priority_id=priority.id,
        amount=payload.amount, charges=Decimal("0.00"), net_amount=Decimal("0.00"),
        remarks=payload.remarks,
    )
    db.add(transaction); db.flush()
    db.add(GoalAllocation(user_id=uid,goal_id=goal.id,account_id=account.id,
                          transaction_id=transaction.id,amount=-payload.amount if release else payload.amount,
                          request_key=payload.request_key))
    goal.saved_amount = (goal.saved_amount or Decimal(0)) + (-payload.amount if release else payload.amount)
    if goal.saved_amount >= goal.target_amount:
        goal.is_completed=True; goal.completed_at=date.today()
    elif goal.is_completed:
        goal.is_completed=False; goal.completed_at=None
    db.commit(); db.refresh(goal)
    return goal



# --------------------------------------------------------------------------
# Removal helpers
#
# Goal allocations are VIRTUAL reservations: change_savings() never touches
# Account.current_balance, it only writes a Goal Saving transaction + a ledger
# row. So removing a goal transaction / goal must NOT run the normal
# transaction_service.delete_transaction() balance reversal (that would credit
# money that was never debited). Removing the ledger row alone is what gives the
# "reserved" money back to the usable balance.
# --------------------------------------------------------------------------
def _refresh_completion(goal: Goal) -> None:
    if goal.saved_amount >= goal.target_amount:
        if not goal.is_completed:
            goal.is_completed = True
            goal.completed_at = date.today()
    elif goal.is_completed:
        goal.is_completed = False
        goal.completed_at = None


def purge_goal_allocations(db: Session, goal: Goal) -> int:
    """Delete every ledger row of a goal and its linked Goal Saving transactions.

    Balance-neutral. Caller deletes the goal itself and commits.
    """
    allocations = db.query(GoalAllocation).filter(GoalAllocation.goal_id == goal.id).all()
    txn_ids = [a.transaction_id for a in allocations]
    for allocation in allocations:
        db.delete(allocation)
    db.flush()  # ledger rows reference transactions (RESTRICT) -> delete them first
    if txn_ids:
        db.query(Transaction).filter(Transaction.id.in_(txn_ids)).delete(synchronize_session=False)
    db.flush()
    return len(txn_ids)


def _txn_snapshot(txn: Transaction) -> dict:
    return {
        "transaction_code": txn.transaction_code, "reference_no": txn.reference_no,
        "txn_date": txn.txn_date.isoformat(), "txn_time": txn.txn_time.isoformat(),
        "description": txn.description, "amount": str(txn.amount),
        "account_slug": txn.from_account_slug, "payment_method_slug": txn.payment_method_slug,
        "priority_slug": txn.priority_slug, "remarks": txn.remarks,
    }


def allocation_snapshot(allocation: GoalAllocation) -> dict:
    """JSON-safe snapshot of one ledger row + its Goal Saving transaction (used by History/Undo)."""
    return {"allocation_amount": str(allocation.amount), "request_key": allocation.request_key,
            **_txn_snapshot(allocation.transaction)}


def ledger_snapshot(db: Session, goal: Goal) -> list[dict]:
    rows = db.query(GoalAllocation).filter(GoalAllocation.goal_id == goal.id).order_by(GoalAllocation.id).all()
    return [allocation_snapshot(r) for r in rows]


def delete_goal_transaction(db: Session, uid: int, txn: Transaction) -> dict:
    """Delete ONE Goal Saving / Release transaction and undo its effect on the goal.

    Returns the history snapshot. Does NOT commit - the caller logs the history
    entry and commits, so both land in the same DB transaction.
    """
    from app.models.user import User
    db.query(User).filter(User.id == uid).with_for_update().one()  # same lock order as change_savings

    allocation = db.query(GoalAllocation).filter(
        GoalAllocation.user_id == uid, GoalAllocation.transaction_id == txn.id).first()
    if allocation is None:
        raise HTTPException(404, "Goal allocation record not found.")
    goal = db.query(Goal).filter(Goal.id == allocation.goal_id).with_for_update().one()
    account = db.query(Account).filter(Account.id == allocation.account_id).with_for_update().one()
    amount = Decimal(allocation.amount)  # +ve = savings added, -ve = savings released

    held_goal_account = Decimal(db.query(func.coalesce(func.sum(GoalAllocation.amount), 0)).filter(
        GoalAllocation.user_id == uid, GoalAllocation.goal_id == goal.id,
        GoalAllocation.account_id == account.id).scalar() or 0)
    if held_goal_account - amount < 0:
        # e.g. deleting an old "add" while a later "release" of that money still exists
        raise HTTPException(409, "A later release depends on this record. Delete the later release record first.")
    if amount < 0:
        # Deleting a release re-reserves the money, so it must still be available.
        held_account = total_reservations(db, uid, account.id)
        if account.current_balance - (held_account - amount) < 0:
            raise HTTPException(400, "Not enough unreserved balance in this account to restore this reservation.")
    new_saved = (goal.saved_amount or Decimal(0)) - amount
    if new_saved < 0:
        raise HTTPException(409, "Deleting this record would make the goal's saved amount negative.")

    snapshot = {"goal_allocation": {"goal_slug": goal.slug, **allocation_snapshot(allocation)}}
    db.delete(allocation)
    db.flush()
    db.delete(txn)
    goal.saved_amount = new_saved
    _refresh_completion(goal)
    db.flush()
    return snapshot


def _recreate_allocation(db: Session, uid: int, goal: Goal, snap: dict) -> GoalAllocation:
    """Re-insert one Goal Saving transaction + ledger row from a history snapshot.

    Account balances are never touched (reservations are virtual).
    """
    from datetime import time as dtime
    from uuid import uuid4
    account = db.query(Account).filter(
        Account.user_id == uid, Account.slug == snap.get("account_slug"), Account.is_system.is_(False)
    ).with_for_update().first()
    if not account:
        raise HTTPException(409, "The account used by this savings record no longer exists.")
    try:
        method = lookups.payment_method_crud.get(db, uid, snap.get("payment_method_slug"))
        priority = lookups.priority_crud.get(db, uid, snap.get("priority_slug"))
    except HTTPException:
        raise HTTPException(409, "The payment method or priority used by this savings record no longer exists.")
    kind, cat, _ = _defaults(db, uid)
    txn_date = date.fromisoformat(snap["txn_date"])
    code = snap.get("transaction_code")
    if not code or db.query(Transaction.id).filter(Transaction.transaction_code == code).first():
        code = generate_transaction_code(db, uid, txn_date)
    key = snap.get("request_key") or ""
    if not key or db.query(GoalAllocation.id).filter_by(user_id=uid, request_key=key).first():
        key = f"restored-{uuid4().hex[:20]}"
    txn = Transaction(
        user_id=uid, transaction_code=code, reference_no=snap.get("reference_no"),
        txn_date=txn_date, txn_time=dtime.fromisoformat(snap["txn_time"]),
        transaction_type_id=kind.id, category_id=cat.id, description=snap["description"],
        from_account_id=account.id, payment_method_id=method.id, priority_id=priority.id,
        amount=Decimal(snap["amount"]), charges=Decimal("0.00"), net_amount=Decimal("0.00"),
        remarks=snap.get("remarks"),
    )
    db.add(txn)
    db.flush()
    allocation = GoalAllocation(user_id=uid, goal_id=goal.id, account_id=account.id, transaction_id=txn.id,
                                amount=Decimal(snap["allocation_amount"]), request_key=key)
    db.add(allocation)
    db.flush()
    return allocation


def _assert_reservations_valid(db: Session, uid: int, goal: Goal, allocations: list[GoalAllocation]) -> None:
    """After a restore: nothing negative, and no account is over-reserved."""
    for account_id in {a.account_id for a in allocations}:
        account = db.query(Account).filter(Account.id == account_id).first()
        held_goal = Decimal(db.query(func.coalesce(func.sum(GoalAllocation.amount), 0)).filter(
            GoalAllocation.user_id == uid, GoalAllocation.goal_id == goal.id,
            GoalAllocation.account_id == account_id).scalar() or 0)
        if held_goal < 0:
            raise HTTPException(409, "Cannot restore: this would release more than the goal holds in that account.")
        if account.current_balance - total_reservations(db, uid, account_id) < 0:
            raise HTTPException(409, f"Cannot restore: not enough unreserved balance in '{account.name}'.")


def restore_goal_ledger(db: Session, uid: int, goal: Goal, allocations: list[dict]) -> None:
    """Undo of a goal DELETE: bring back every savings/release transaction + ledger row.

    The goal row itself (incl. saved_amount) is restored by the caller from the snapshot.
    """
    if not allocations:
        return
    from app.models.user import User
    db.query(User).filter(User.id == uid).with_for_update().one()
    created = [_recreate_allocation(db, uid, goal, snap) for snap in allocations]
    _assert_reservations_valid(db, uid, goal, created)


def restore_goal_transaction(db: Session, uid: int, snapshot: dict) -> None:
    """Undo of deleting ONE goal savings/release transaction."""
    from app.models.user import User
    db.query(User).filter(User.id == uid).with_for_update().one()
    data = snapshot["goal_allocation"]
    goal = db.query(Goal).filter(Goal.user_id == uid, Goal.slug == data.get("goal_slug")).with_for_update().first()
    if goal is None:
        raise HTTPException(409, "The goal for this savings record no longer exists. Restore the goal first.")
    allocation = _recreate_allocation(db, uid, goal, data)
    goal.saved_amount = (goal.saved_amount or Decimal(0)) + Decimal(data["allocation_amount"])
    _refresh_completion(goal)
    _assert_reservations_valid(db, uid, goal, [allocation])
    db.flush()
