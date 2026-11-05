"""
Transaction creation / update / deletion business engine.

Backend redesign changes vs. the original version:
  - All related entities are now referenced by SLUG in the payload, never
    raw id (Loan keeps its human-readable loan_code; CreditPurchase uses
    its slug like everything else).
  - Account requirement is NATURE-based, not a hardcoded name list:
    inflow -> to_account only, outflow -> from_account only,
    transfer -> both. This fixes a real bug where any custom/new
    transaction type (not in the old hardcoded boundary-name sets) was
    incorrectly forced to require both accounts like a transfer.
  - `charges_borne_by` ("from_account" | "to_account") controls how a
    transfer's charges affect the two balances, and pure-inflow charges
    now actually reduce what lands in the receiving account (previously
    charges had zero effect on real balances for inflow transactions —
    only on the net_amount reporting figure).
  - Item Payment Made / Item Payment Received settle a CreditPurchase,
    completely separate from the Loan settlement code path (per explicit
    instruction: never mix the two systems, even though the mechanics
    rhyme).
"""
from datetime import datetime, timezone, date as date_cls
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.models.transaction_type import TransactionType
from app.models.loan import Loan
from app.models.credit_purchase import CreditPurchase
from app.models.budget import Budget
from app.models.account import Account
from app.schemas.transaction import TransactionCreate
from app.utils.codes import generate_transaction_code, generate_loan_code
from app.crud import lookups

LOAN_TRANSACTION_TYPES = {"loan received", "loan given", "loan repaid", "loan collected"}
NEW_LOAN_TYPES = {"loan received", "loan given"}
SETTLEMENT_LOAN_TYPES = {"loan repaid", "loan collected"}

CREDIT_PURCHASE_SETTLEMENT_TYPES = {"item payment made", "item payment received"}


def _norm(name: str) -> str:
    return name.strip().lower()


def _resolve_loan(db: Session, user_id: int, loan_code: str) -> Loan:
    loan = db.execute(select(Loan).where(Loan.loan_code == loan_code, Loan.user_id == user_id)).scalar_one_or_none()
    if not loan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loan not found.")
    return loan


def _resolve_credit_purchase(db: Session, user_id: int, slug: str) -> CreditPurchase:
    cp = db.execute(select(CreditPurchase).where(CreditPurchase.slug == slug, CreditPurchase.user_id == user_id)).scalar_one_or_none()
    if not cp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credit purchase not found.")
    return cp


def create_transaction(db: Session, user_id: int, payload: TransactionCreate, *, commit: bool = True) -> Transaction:
    # ---- 1. Resolve & validate transaction type + category ----
    txn_type = lookups.transaction_type_crud.get(db, user_id, payload.transaction_type_slug)
    if not txn_type.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Transaction type is inactive.")
    category = lookups.category_crud.get(db, user_id, payload.category_slug)
    lookups.assert_category_linked_to_type(db, user_id, category.id, txn_type.id)

    type_name = _norm(txn_type.name)
    if type_name == "goal saving":
        raise HTTPException(status_code=400, detail="Use the goal Add Savings action to reserve funds.")
    is_loan_txn = type_name in LOAN_TRANSACTION_TYPES
    is_credit_purchase_settlement = type_name in CREDIT_PURCHASE_SETTLEMENT_TYPES

    # ---- 2. Contact validation (group is optional — see lookups.assert_contact_valid_for_category) ----
    contact = None
    if payload.contact_slug is not None:
        contact = lookups.contact_crud.get(db, user_id, payload.contact_slug)
        lookups.assert_contact_valid_for_category(db, user_id, contact.id, category.id)
    if is_loan_txn and payload.contact_slug is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="contact_slug is mandatory for loan transaction types.")

    # ---- 3. Loan logic ----
    loan: Loan | None = None
    if type_name in NEW_LOAN_TYPES:
        loan_type = "received" if type_name == "loan received" else "given"
        loan = Loan(
            user_id=user_id,
            loan_code=generate_loan_code(db, user_id, payload.txn_date),
            loan_type=loan_type,
            contact_id=contact.id,
            principal_amount=payload.amount,
            remaining_amount=payload.amount,
            due_date=payload.due_date,
        )
        db.add(loan)
        db.flush()
    elif type_name in SETTLEMENT_LOAN_TYPES:
        if not payload.loan_code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"loan_code is required for '{txn_type.name}'.")
        loan = _resolve_loan(db, user_id, payload.loan_code)
        expected_loan_type = "received" if type_name == "loan repaid" else "given"
        if loan.loan_type != expected_loan_type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{txn_type.name}' requires a loan of type '{expected_loan_type}'.")
        if loan.is_completed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This loan is already completed.")
        if payload.amount > loan.remaining_amount:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Amount ({payload.amount}) exceeds remaining loan balance ({loan.remaining_amount}).")

    # ---- 3b. Credit purchase settlement logic (deliberately separate from loans) ----
    credit_purchase: CreditPurchase | None = None
    if is_credit_purchase_settlement:
        if not payload.credit_purchase_slug:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"credit_purchase_slug is required for '{txn_type.name}'.")
        credit_purchase = _resolve_credit_purchase(db, user_id, payload.credit_purchase_slug)
        expected_credit_type = "received" if type_name == "item payment made" else "given"
        if credit_purchase.credit_type != expected_credit_type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{txn_type.name}' requires a credit purchase of type '{expected_credit_type}'.")
        if credit_purchase.is_completed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This credit purchase is already fully settled.")
        if payload.amount > credit_purchase.remaining_amount:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Amount ({payload.amount}) exceeds remaining balance ({credit_purchase.remaining_amount}).")

    # ---- 4. From/To account resolution — NATURE-based (fixes the old hardcoded-name-list bug) ----
    from_account_id = lookups.account_crud.get(db, user_id, payload.from_account_slug).id if payload.from_account_slug else None
    to_account_id = lookups.account_crud.get(db, user_id, payload.to_account_slug).id if payload.to_account_slug else None

    if txn_type.nature == "inflow":
        external = lookups.get_or_create_external_account(db, user_id)
        from_account_id = external.id
        if to_account_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="to_account_slug is required for an inflow transaction type.")
    elif txn_type.nature == "outflow":
        external = lookups.get_or_create_external_account(db, user_id)
        to_account_id = external.id
        if from_account_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from_account_slug is required for an outflow transaction type.")
    else:  # transfer
        if from_account_id is None or to_account_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Both from_account_slug and to_account_slug are required for a transfer-nature transaction type.")

    from_account = lookups.assert_account_owned(db, user_id, from_account_id)
    to_account = lookups.assert_account_owned(db, user_id, to_account_id)

    # ---- 5. Payment method must belong to the account the user actually controls ----
    if txn_type.nature == "inflow":
        controlling_account_id = to_account_id
    else:  # outflow or transfer — from_account is the user-initiated side
        controlling_account_id = from_account_id
    payment_method = lookups.payment_method_crud.get(db, user_id, payload.payment_method_slug)
    lookups.assert_payment_method_matches_account(db, user_id, payment_method.id, controlling_account_id)

    # ---- 6. Priority must exist ----
    priority = lookups.priority_crud.get(db, user_id, payload.priority_slug)

    # ---- 7. Net amount ----
    total = payload.amount + payload.charges
    if txn_type.nature == "inflow":
        net_amount = total
    elif txn_type.nature == "outflow":
        net_amount = -total
    else:
        net_amount = Decimal("0.00")

    # ---- 8. Transaction code ----
    txn_code = generate_transaction_code(db, user_id, payload.txn_date)

    transaction = Transaction(
        user_id=user_id,
        transaction_code=txn_code,
        reference_no=payload.reference_no,
        txn_date=payload.txn_date,
        txn_time=payload.txn_time,
        transaction_type_id=txn_type.id,
        category_id=category.id,
        loan_id=loan.id if loan else None,
        credit_purchase_id=credit_purchase.id if credit_purchase else None,
        description=payload.description,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        payment_method_id=payment_method.id,
        contact_id=contact.id if contact else None,
        priority_id=priority.id,
        amount=payload.amount,
        charges=payload.charges,
        charges_borne_by=payload.charges_borne_by,
        net_amount=net_amount,
        remarks=payload.remarks,
    )
    db.add(transaction)

    # ---- 9. Apply balance updates ----
    if txn_type.nature == "inflow":
        # Only to_account is real; from_account is the synthetic External
        # account. Charges now actually reduce what's credited — a
        # processing fee on an incoming payment means less lands in the
        # account, not just a bigger number in reporting.
        to_account.current_balance += (payload.amount - payload.charges)
        from_account.current_balance -= total
    elif txn_type.nature == "outflow":
        # Only from_account is real; to_account is External.
        from_account.current_balance -= total
        to_account.current_balance += payload.amount
    else:  # transfer — both accounts are real
        if payload.charges_borne_by == "to_account":
            # Fee skimmed off what arrives (e.g. agent withdrawal: send
            # 1000, receive 980).
            from_account.current_balance -= payload.amount
            to_account.current_balance += (payload.amount - payload.charges)
        else:
            # Fee added on top of what the sender pays (e.g. bank adds a
            # transfer fee).
            from_account.current_balance -= total
            to_account.current_balance += payload.amount

    # ---- 10. Loan / credit-purchase remaining-balance updates for settlements ----
    if loan is not None and type_name in SETTLEMENT_LOAN_TYPES:
        loan.remaining_amount -= payload.amount
        if loan.remaining_amount <= 0:
            loan.remaining_amount = Decimal("0.00")
            loan.is_completed = True
            loan.completed_at = datetime.now(timezone.utc)

    if credit_purchase is not None:
        credit_purchase.remaining_amount -= payload.amount
        if credit_purchase.remaining_amount <= 0:
            credit_purchase.remaining_amount = Decimal("0.00")
            credit_purchase.is_completed = True
            credit_purchase.completed_at = datetime.now(timezone.utc)

    db.flush()

    # ---- 11. Budget auto-tracking — only for Expense-type transactions ----
    if type_name == "expense":
        _apply_budget_tracking(db, user_id, category.id, payload.txn_date, payload.amount)

    if commit:
        db.commit()
        db.refresh(transaction)
    return transaction


def _apply_budget_tracking(db: Session, user_id: int, category_id: int, txn_date: date_cls, amount: Decimal) -> None:
    stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.category_id == category_id,
        Budget.is_active == True,  # noqa: E712
        Budget.start_date <= txn_date,
    ).where(or_(Budget.end_date.is_(None), Budget.end_date >= txn_date))
    budget = db.execute(stmt).scalar_one_or_none()
    if budget:
        budget.spent_amount = (budget.spent_amount or Decimal("0.00")) + amount


def get_transaction_by_code(db: Session, user_id: int, transaction_code: str) -> Transaction:
    stmt = select(Transaction).where(
        Transaction.transaction_code == transaction_code,
        Transaction.user_id == user_id,
        Transaction.is_active == True,  # noqa: E712
    )
    txn = db.execute(stmt).scalar_one_or_none()
    if not txn:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
    return txn


def check_transaction_deletion(db: Session, user_id: int, transaction_code: str) -> dict:
    """
    Loan chain integrity only (credit purchases have no equivalent
    concern — a settlement transaction is never depended on by anything
    else, and CreditPurchase creation itself was never a transaction in
    the first place, so there's no "opening transaction" to protect).
    """
    txn = get_transaction_by_code(db, user_id, transaction_code)
    if txn.loan_id is None:
        return {"blocked": False, "message": None, "blocking_transactions": []}

    txn_type = db.execute(select(TransactionType).where(TransactionType.id == txn.transaction_type_id)).scalar_one_or_none()
    type_name = _norm(txn_type.name)
    if type_name == "goal saving":
        raise HTTPException(status_code=400, detail="Use the goal Add Savings action to reserve funds.") if txn_type else ""
    if type_name not in NEW_LOAN_TYPES:
        return {"blocked": False, "message": None, "blocking_transactions": []}

    loan = db.execute(select(Loan).where(Loan.id == txn.loan_id)).scalar_one_or_none()

    stmt = select(Transaction).where(
        Transaction.loan_id == txn.loan_id,
        Transaction.id != txn.id,
        Transaction.user_id == user_id,
        Transaction.is_active == True,  # noqa: E712
    )
    dependents = list(db.execute(stmt).scalars().all())
    if not dependents:
        return {"blocked": False, "message": None, "blocking_transactions": []}

    settle_word = "Loan Repaid" if type_name == "loan received" else "Loan Collected"
    dep_list = ", ".join(f"{d.transaction_code} ({settle_word}, {d.amount})" for d in dependents)
    loan_code = loan.loan_code if loan else f"#{txn.loan_id}"
    message = (
        f"Cannot delete {txn.transaction_code} ({txn_type.name}): loan {loan_code} has "
        f"{len(dependents)} settlement transaction(s) recorded against it — delete {dep_list} first."
    )
    return {
        "blocked": True,
        "message": message,
        "blocking_transactions": [
            {"transaction_code": d.transaction_code, "amount": float(d.amount)} for d in dependents
        ],
    }


def delete_transaction(db: Session, user_id: int, transaction_code: str) -> None:
    """
    Reverses a transaction's financial effects (account balances, loan or
    credit-purchase remaining amount, budget spent_amount) and then
    HARD-deletes the row. Mirrors create_transaction()'s nature/
    charges_borne_by-aware math exactly, in reverse.

    Callers MUST run check_transaction_deletion() first and surface a 409
    if it's blocked — this function assumes that check already passed.
    """
    txn = get_transaction_by_code(db, user_id, transaction_code)

    from_account = db.execute(select(Account).where(Account.id == txn.from_account_id)).scalar_one_or_none()
    to_account = db.execute(select(Account).where(Account.id == txn.to_account_id)).scalar_one_or_none()
    txn_type = db.execute(select(TransactionType).where(TransactionType.id == txn.transaction_type_id)).scalar_one_or_none()
    total = txn.amount + txn.charges

    if txn_type and txn_type.nature == "inflow":
        if to_account:
            to_account.current_balance -= (txn.amount - txn.charges)
        if from_account:
            from_account.current_balance += total
    elif txn_type and txn_type.nature == "outflow":
        if from_account:
            from_account.current_balance += total
        if to_account:
            to_account.current_balance -= txn.amount
    else:  # transfer
        if txn.charges_borne_by == "to_account":
            if from_account:
                from_account.current_balance += txn.amount
            if to_account:
                to_account.current_balance -= (txn.amount - txn.charges)
        else:
            if from_account:
                from_account.current_balance += total
            if to_account:
                to_account.current_balance -= txn.amount

    loan_to_delete = None
    if txn.loan_id:
        loan = db.execute(select(Loan).where(Loan.id == txn.loan_id)).scalar_one_or_none()
        type_name = _norm(txn_type.name) if txn_type else ""
        if type_name in SETTLEMENT_LOAN_TYPES and loan:
            loan.remaining_amount += txn.amount
            if loan.is_completed and loan.remaining_amount > 0:
                loan.is_completed = False
                loan.completed_at = None
        elif type_name in NEW_LOAN_TYPES and loan:
            # check_transaction_deletion() guarantees no settlements exist.
            loan_to_delete = loan

    if txn.credit_purchase_id:
        credit_purchase = db.execute(select(CreditPurchase).where(CreditPurchase.id == txn.credit_purchase_id)).scalar_one_or_none()
        if credit_purchase:
            credit_purchase.remaining_amount += txn.amount
            if credit_purchase.is_completed and credit_purchase.remaining_amount > 0:
                credit_purchase.is_completed = False
                credit_purchase.completed_at = None

    if txn_type and _norm(txn_type.name) == "expense":
        stmt = select(Budget).where(
            Budget.user_id == user_id,
            Budget.category_id == txn.category_id,
            Budget.start_date <= txn.txn_date,
        ).where(or_(Budget.end_date.is_(None), Budget.end_date >= txn.txn_date))
        budget = db.execute(stmt).scalar_one_or_none()
        if budget:
            budget.spent_amount = max(Decimal("0.00"), (budget.spent_amount or Decimal("0.00")) - txn.amount)

    # AI-confirmed transactions have a draft.resulting_transaction_id FK.
    # Clearing this link first allows the EXACT SAME normal delete/Undo
    # behavior as manually created transactions without violating FK rules.
    # Drafts remain confirmed so they cannot be posted twice.
    from app.models.transaction_draft import TransactionDraft
    from sqlalchemy import update
    db.execute(update(TransactionDraft).where(
        TransactionDraft.user_id == user_id,
        TransactionDraft.resulting_transaction_id == txn.id,
    ).values(resulting_transaction_id=None))

    db.delete(txn)
    db.flush()
    if loan_to_delete is not None:
        db.delete(loan_to_delete)
    db.commit()
