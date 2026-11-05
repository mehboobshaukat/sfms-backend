"""
All figures computed live via aggregate queries — no static summary
table. Every query is indexed on user_id, txn_date, transaction_type_id,
category_id, loan_id.
"""
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case, select, true
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.transaction import Transaction
from app.models.transaction_type import TransactionType
from app.models.category import Category
from app.models.loan import Loan
from app.models.budget import Budget
from app.models.goal import Goal
from app.models.credit_purchase import CreditPurchase
from app.crud.lookups import account_crud
from app.crud.goal_savings_service import total_reservations
from app.schemas.account import AccountOut
from app.schemas.transaction import TransactionOut

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# Sentinel "beginning of time" for the lifetime range — simpler and just
# as correct as querying MIN(txn_date), since any real txn_date will be
# well after this.
_EPOCH = date(1970, 1, 1)


def _date_range(range_key: str | None, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = date.today()
    if date_from and date_to:
        return date_from, date_to
    range_key = (range_key or "month").lower()
    if range_key == "today":
        return today, today
    if range_key == "week":
        return today - timedelta(days=today.weekday()), today
    if range_key == "year":
        return today.replace(month=1, day=1), today
    if range_key == "lifetime":
        return _EPOCH, today
    # default: month
    return today.replace(day=1), today


@router.get("/summary")
def analytics_summary(
    range: str | None = Query(default="month", description="today|week|month|year|lifetime|custom"),
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Dashboard summary cards: total inflow, total outflow, net savings, active loans, budget/goal status."""
    start, end = _date_range(range, date_from, date_to)

    # Consolidate the seven small independent COUNT/SUM round trips into
    # one network round trip. Scalar subqueries preserve each table's own
    # filters without introducing multi-table Cartesian product errors.
    tx = (
        select(
            func.coalesce(func.sum(case((TransactionType.nature == "inflow", Transaction.net_amount), else_=0)), 0).label("inflow"),
            func.coalesce(func.sum(case((TransactionType.nature == "outflow", -Transaction.net_amount), else_=0)), 0).label("outflow"),
        )
        .select_from(Transaction)
        .join(TransactionType, Transaction.transaction_type_id == TransactionType.id)
        .where(
            Transaction.user_id == current_user.id,
            Transaction.is_active.is_(True),
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
        )
        .subquery()
    )
    loan_counts = (
        select(
            func.coalesce(func.sum(case((Loan.loan_type == "received", 1), else_=0)), 0).label("received"),
            func.coalesce(func.sum(case((Loan.loan_type == "given", 1), else_=0)), 0).label("given"),
        )
        .where(Loan.user_id == current_user.id, Loan.is_completed.is_(False))
        .subquery()
    )
    today = date.today()
    budgets_over_query = (
        select(func.count(Budget.id))
        .where(
            Budget.user_id == current_user.id,
            Budget.is_active.is_(True),
            Budget.start_date <= today,
            (Budget.end_date.is_(None)) | (Budget.end_date >= today),
            Budget.spent_amount > Budget.amount,
        )
        .scalar_subquery()
    )
    goals_active_query = (
        select(func.count(Goal.id))
        .where(Goal.user_id == current_user.id, Goal.is_completed.is_(False))
        .scalar_subquery()
    )
    credit = (
        select(
            func.coalesce(func.sum(case((CreditPurchase.credit_type == "received", CreditPurchase.remaining_amount), else_=0)), 0).label("owed"),
            func.coalesce(func.sum(case((CreditPurchase.credit_type == "given", CreditPurchase.remaining_amount), else_=0)), 0).label("receivable"),
        )
        .where(CreditPurchase.user_id == current_user.id, CreditPurchase.is_completed.is_(False))
        .subquery()
    )
    metrics = db.execute(
        select(
            tx.c.inflow,
            tx.c.outflow,
            loan_counts.c.received,
            loan_counts.c.given,
            budgets_over_query.label("budgets_over"),
            goals_active_query.label("goals_active"),
            credit.c.owed,
            credit.c.receivable,
        ).select_from(tx.join(loan_counts, true()).join(credit, true()))
    ).one()
    inflow, outflow = metrics.inflow, metrics.outflow
    active_loans_received, active_loans_given = int(metrics.received), int(metrics.given)
    budgets_over, goals_active = int(metrics.budgets_over), int(metrics.goals_active)
    credit_totals = {
        "pending_owed_total": metrics.owed,
        "pending_receivable_total": metrics.receivable,
    }

    return {
        "period": {"from": start, "to": end},
        "currency_code": current_user.currency_code,
        "currency_symbol": current_user.currency_symbol,
        "total_inflow": float(inflow),
        "total_outflow": float(outflow),
        "net_savings": float(inflow) - float(outflow),
        "active_loans_received": active_loans_received,
        "active_loans_given": active_loans_given,
        "budgets_over_limit": budgets_over,
        "active_goals": goals_active,
        "pending_owed_total": float(credit_totals["pending_owed_total"]),
        "pending_receivable_total": float(credit_totals["pending_receivable_total"]),
    }


@router.get("/transactions")
def analytics_transactions(
    range: str | None = Query(default="month", description="today|week|month|year|lifetime|custom"),
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Charts: income vs expense trend (daily), category-wise breakdown (pie)."""
    start, end = _date_range(range, date_from, date_to)

    daily_trend = (
        db.query(
            Transaction.txn_date,
            func.coalesce(func.sum(case((TransactionType.nature == "inflow", Transaction.net_amount), else_=0)), 0).label("inflow"),
            func.coalesce(func.sum(case((TransactionType.nature == "outflow", -Transaction.net_amount), else_=0)), 0).label("outflow"),
        )
        .join(TransactionType, Transaction.transaction_type_id == TransactionType.id)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.is_active == True,  # noqa: E712
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
        )
        .group_by(Transaction.txn_date)
        .order_by(Transaction.txn_date)
        .all()
    )

    category_breakdown = (
        db.query(Category.name, func.coalesce(func.sum(Transaction.amount), 0).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.is_active == True,  # noqa: E712
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
        )
        .group_by(Category.name)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    return {
        "period": {"from": start, "to": end},
        "daily_trend": [{"date": r.txn_date, "inflow": float(r.inflow), "outflow": float(r.outflow)} for r in daily_trend],
        "category_breakdown": [{"category": r.name, "total": float(r.total)} for r in category_breakdown],
    }


@router.get("/loans")
def analytics_loans(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Loan analysis: active loans by contact, pending received vs given, repayment history per loan."""
    loans = db.query(Loan).options(joinedload(Loan.contact)).filter(Loan.user_id == current_user.id).all()
    pending_received = sum(float(l.remaining_amount) for l in loans if l.loan_type == "received" and not l.is_completed)
    pending_given = sum(float(l.remaining_amount) for l in loans if l.loan_type == "given" and not l.is_completed)

    by_contact: dict[str, dict] = {}
    for l in loans:
        entry = by_contact.setdefault(l.contact_slug, {"contact_slug": l.contact_slug, "received": 0.0, "given": 0.0})
        if not l.is_completed:
            if l.loan_type == "received":
                entry["received"] += float(l.remaining_amount)
            else:
                entry["given"] += float(l.remaining_amount)

    return {
        "pending_received_total": pending_received,
        "pending_given_total": pending_given,
        "active_by_contact": list(by_contact.values()),
        "loans": [
            {
                "loan_code": l.loan_code,
                "loan_type": l.loan_type,
                "contact_slug": l.contact_slug,
                "principal_amount": float(l.principal_amount),
                "remaining_amount": float(l.remaining_amount),
                "is_completed": l.is_completed,
                "due_date": l.due_date,
            }
            for l in loans
        ],
    }


@router.get("/budgets")
def analytics_budgets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Budget analysis: budget vs actual per category."""
    budgets = db.query(Budget).options(joinedload(Budget.category)).filter(Budget.user_id == current_user.id, Budget.is_active == True).all()  # noqa: E712
    return [
        {
            "slug": b.slug,
            "category_slug": b.category_slug,
            "period": b.period,
            "amount": float(b.amount),
            "spent_amount": float(b.spent_amount),
            "remaining": float(b.amount - b.spent_amount),
            "percent_used": round(float(b.spent_amount / b.amount * 100), 2) if b.amount else 0.0,
            "start_date": b.start_date,
            "end_date": b.end_date,
        }
        for b in budgets
    ]


@router.get("/goals")
def analytics_goals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Goal analysis: progress %, time-to-target, grouped by priority."""
    goals = db.query(Goal).options(joinedload(Goal.priority)).filter(Goal.user_id == current_user.id).all()
    today = date.today()
    return [
        {
            "slug": g.slug,
            "name": g.name,
            "priority_slug": g.priority_slug,
            "target_amount": float(g.target_amount),
            "saved_amount": float(g.saved_amount),
            "progress_percent": round(float(g.saved_amount / g.target_amount * 100), 2) if g.target_amount else 0.0,
            "days_remaining": (g.target_date - today).days if g.target_date else None,
            "is_completed": g.is_completed,
        }
        for g in goals
    ]


@router.get("/report")
def analytics_report(
    date_from: date,
    date_to: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full combined report for a custom date range — export-ready payload."""
    summary = analytics_summary(range="custom", date_from=date_from, date_to=date_to, current_user=current_user, db=db)
    txns = analytics_transactions(range="custom", date_from=date_from, date_to=date_to, current_user=current_user, db=db)
    loans = analytics_loans(current_user=current_user, db=db)
    budgets = analytics_budgets(current_user=current_user, db=db)
    goals = analytics_goals(current_user=current_user, db=db)
    return {"summary": summary, "transactions": txns, "loans": loans, "budgets": budgets, "goals": goals}


@router.get("/dashboard")
def analytics_dashboard(
    range: str | None = Query(default="month", description="today|week|month|year|lifetime|custom"),
    date_from: date | None = None,
    date_to: date | None = None,
    recent_limit: int = Query(default=5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Single round-trip payload for the authenticated dashboard.

    Replaces the previous pattern of ~9 browser → API → remote-DB hops
    (summary, trends, recent txns, accounts, loans, credit totals, budgets,
    goals, savings summary) with one authenticated call. Individual
    /analytics/* endpoints remain available for other pages.
    """
    # Lazy import avoids a circular import at module load time: transactions
    # endpoints do not import analytics, but sharing eager-load options keeps
    # recent-transaction serialization free of N+1 queries.
    from app.api.v1.endpoints.transactions import _TXN_RELATIONS

    summary = analytics_summary(
        range=range, date_from=date_from, date_to=date_to, current_user=current_user, db=db
    )
    trends = analytics_transactions(
        range=range, date_from=date_from, date_to=date_to, current_user=current_user, db=db
    )
    # Dashboard only displays active loan totals. Avoid loading every historical
    # loan and its contact (that detailed response remains on /analytics/loans).
    loan_totals = db.query(
        func.coalesce(func.sum(case((Loan.loan_type == "received", Loan.remaining_amount), else_=0)), 0),
        func.coalesce(func.sum(case((Loan.loan_type == "given", Loan.remaining_amount), else_=0)), 0),
    ).filter(Loan.user_id == current_user.id, Loan.is_completed.is_(False)).one()
    loans = {
        "pending_received_total": float(loan_totals[0]),
        "pending_given_total": float(loan_totals[1]),
    }
    budgets = analytics_budgets(current_user=current_user, db=db)
    goals = analytics_goals(current_user=current_user, db=db)

    # Reuse the exact account objects used for the account widget to compute
    # financial funds. Previously a second SELECT loaded them again.
    accounts = [
        a for a in account_crud.list(db, current_user.id, active_only=False) if not a.is_system
    ]
    accounts_out = [AccountOut.model_validate(a).model_dump(mode="json") for a in accounts]

    recent_query = db.query(Transaction).filter(
        Transaction.user_id == current_user.id,
        Transaction.is_active.is_(True),
    )
    recent_items = (
        recent_query.options(*_TXN_RELATIONS)
        .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .limit(recent_limit)
        .all()
    )
    recent_out = [
        TransactionOut.model_validate(item).model_dump(mode="json") for item in recent_items
    ]

    # Summary already includes the exact same user-scoped credit totals;
    # do not query the credit purchase table a second time.
    credit_totals = {
        "pending_owed_total": summary["pending_owed_total"],
        "pending_receivable_total": summary["pending_receivable_total"],
    }

    actual = sum((a.current_balance for a in accounts), Decimal("0.00"))
    reserved = total_reservations(db, current_user.id)
    savings_summary = {
        "goal_savings": float(reserved),
        "usable_balance": float(actual - reserved),
        "total_financial_funds": float(actual),
    }

    return {
        "summary": summary,
        "trends": trends,
        "recent_transactions": {
            "page": 1,
            "page_size": recent_limit,
            "items": recent_out,
        },
        "accounts": accounts_out,
        "loans": loans,
        "credit_totals": credit_totals,
        "budgets": budgets,
        "goals": goals,
        "savings_summary": savings_summary,
    }
