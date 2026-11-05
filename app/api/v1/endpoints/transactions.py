from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.crud import transaction_service, history_service
from app.crud.lookups import transaction_type_crud, category_crud, account_crud, payment_method_crud, priority_crud, contact_crud
from app.models.user import User
from app.models.transaction import Transaction
from app.models.goal_allocation import GoalAllocation
from app.schemas.transaction import TransactionCreate, TransactionUpdate, TransactionOut, TransactionFilter
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/transactions", tags=["Transactions"])

# TransactionOut reads relationship-backed public slugs. Without eager loading,
# each row can produce several additional SELECTs against the remote database.
_TXN_RELATIONS = (
    joinedload(Transaction.transaction_type),
    joinedload(Transaction.category),
    joinedload(Transaction.from_account),
    joinedload(Transaction.to_account),
    joinedload(Transaction.payment_method),
    joinedload(Transaction.contact),
    joinedload(Transaction.priority),
    joinedload(Transaction.loan),
    joinedload(Transaction.credit_purchase),
    joinedload(Transaction.goal_allocation).joinedload(GoalAllocation.goal),
)



@router.get("", response_model=PaginatedResponse[TransactionOut])
def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction).filter(Transaction.user_id == current_user.id, Transaction.is_active == True)  # noqa: E712
    total = query.count()
    items = (
        query.options(*_TXN_RELATIONS).order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return PaginatedResponse(total=total, page=page, page_size=page_size, items=items)


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Full business engine: resolves every related entity by SLUG,
    validates the TransactionType -> Category link (and, if given, a
    Contact against that category's linked groups), resolves From/To
    accounts by NATURE (inflow needs to_account only, outflow needs
    from_account only, transfer needs both — see transaction_service.py),
    runs Loan open/settle logic and CreditPurchase settlement logic
    (kept fully separate from each other), computes net_amount, updates
    balances (charges_borne_by-aware), and applies budget tracking.
    """
    txn = transaction_service.create_transaction(db, current_user.id, payload)
    history_service.log_entry(
        db, current_user.id, "transaction", txn.transaction_code, f"{txn.description} - {txn.amount}",
        action="create", after_snapshot=payload.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(txn)
    return txn


@router.get("/{transaction_code}", response_model=TransactionOut)
def get_transaction(
    transaction_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return transaction_service.get_transaction_by_code(db, current_user.id, transaction_code)


@router.put("/{transaction_code}", response_model=TransactionOut)
def update_transaction(
    transaction_code: str,
    payload: TransactionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Only non-financial fields are editable post-creation (description,
    reference_no, remarks, priority, payment_method). Amount, accounts,
    category, and type are immutable once saved — delete and recreate the
    transaction if those need to change, so balances/loans/budgets never
    drift out of sync.
    """
    txn = transaction_service.get_transaction_by_code(db, current_user.id, transaction_code)
    before_snapshot = {"description": txn.description, "reference_no": txn.reference_no, "remarks": txn.remarks}
    data = payload.model_dump(exclude_unset=True, exclude={"priority_slug", "payment_method_slug"})
    if payload.priority_slug:
        data["priority_id"] = priority_crud.get(db, current_user.id, payload.priority_slug).id
    if payload.payment_method_slug:
        data["payment_method_id"] = payment_method_crud.get(db, current_user.id, payload.payment_method_slug).id
    for key, value in data.items():
        setattr(txn, key, value)
    history_service.log_entry(db, current_user.id, "transaction", txn.transaction_code, f"{txn.description} - {txn.amount}", action="update", before_snapshot=before_snapshot)
    db.commit()
    db.refresh(txn)
    return txn


@router.delete("/{transaction_code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(
    transaction_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Hard delete — automatically reverses balance/loan/credit-purchase/
    budget effects first, then permanently removes the row.

    Blocked (409) if this is a loan-opening transaction (Loan Received /
    Loan Given) and later settlement transactions already exist against
    that loan — those must be deleted first.
    """
    check = transaction_service.check_transaction_deletion(db, current_user.id, transaction_code)
    if check["blocked"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "blocked", "message": check["message"], "blocking_transactions": check["blocking_transactions"]},
        )
    txn = transaction_service.get_transaction_by_code(db, current_user.id, transaction_code)
    if txn.goal_allocation is not None:
        # Virtual reservation: remove ledger row + transaction and fix goal.saved_amount.
        # Never call transaction_service.delete_transaction() here - it would reverse a
        # balance movement that never happened. History keeps a full snapshot so Undo works.
        from app.crud.goal_savings_service import delete_goal_transaction
        label = f"{txn.description} - {txn.amount}"
        snapshot = delete_goal_transaction(db, current_user.id, txn)
        history_service.log_entry(db, current_user.id, "transaction", transaction_code, label,
                                  action="delete", before_snapshot=snapshot)
        db.commit()
        return
    before_snapshot = {
        "txn_date": txn.txn_date.isoformat(), "txn_time": txn.txn_time.isoformat(),
        "transaction_type_slug": txn.transaction_type_slug, "category_slug": txn.category_slug,
        "description": txn.description, "from_account_slug": txn.from_account_slug, "to_account_slug": txn.to_account_slug,
        "payment_method_slug": txn.payment_method_slug, "contact_slug": txn.contact_slug, "priority_slug": txn.priority_slug,
        "amount": float(txn.amount), "charges": float(txn.charges), "charges_borne_by": txn.charges_borne_by,
        "reference_no": txn.reference_no, "remarks": txn.remarks,
    }
    label = f"{txn.description} - {txn.amount}"
    transaction_service.delete_transaction(db, current_user.id, transaction_code)
    history_service.log_entry(db, current_user.id, "transaction", transaction_code, label, action="delete", before_snapshot=before_snapshot)
    db.commit()


@router.post("/filter", response_model=PaginatedResponse[TransactionOut])
def filter_transactions(
    filters: TransactionFilter,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Transaction).filter(Transaction.user_id == current_user.id, Transaction.is_active == True)  # noqa: E712

    if filters.date_from:
        query = query.filter(Transaction.txn_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(Transaction.txn_date <= filters.date_to)
    if filters.transaction_type_slug:
        tt = transaction_type_crud.get(db, current_user.id, filters.transaction_type_slug)
        query = query.filter(Transaction.transaction_type_id == tt.id)
    if filters.category_slug:
        cat = category_crud.get(db, current_user.id, filters.category_slug)
        query = query.filter(Transaction.category_id == cat.id)
    if filters.account_slug:
        acc = account_crud.get(db, current_user.id, filters.account_slug)
        query = query.filter((Transaction.from_account_id == acc.id) | (Transaction.to_account_id == acc.id))
    if filters.payment_method_slug:
        pm = payment_method_crud.get(db, current_user.id, filters.payment_method_slug)
        query = query.filter(Transaction.payment_method_id == pm.id)
    if filters.priority_slug:
        pr = priority_crud.get(db, current_user.id, filters.priority_slug)
        query = query.filter(Transaction.priority_id == pr.id)
    if filters.contact_slug:
        c = contact_crud.get(db, current_user.id, filters.contact_slug)
        query = query.filter(Transaction.contact_id == c.id)

    total = query.count()
    items = (
        query.options(*_TXN_RELATIONS).order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
        .all()
    )
    return PaginatedResponse(total=total, page=filters.page, page_size=filters.page_size, items=items)
