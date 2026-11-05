"""
TransactionDraft CRUD — the staging area itself. FOUNDATION ONLY: no
speech-to-text or LLM call happens anywhere in this file yet.

When that integration is added later, it plugs in as the body of
create_draft() — parse `raw_input` (transcribing it first if
source="voice"), fill `parsed_fields`/`ambiguous_fields`, and everything
else (list/confirm/reject, the review UI, expiry) already works
unchanged.
"""
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.transaction_draft import TransactionDraft
from app.schemas.transaction import TransactionCreate
from app.schemas.draft_bulk import BulkDraftConfirmResult, BulkPostedTransaction, BulkDraftRejectResult
from app.schemas.transaction_draft import TransactionDraftCreate
from app.utils.slugs import generate_slug


def create_draft(db: Session, user_id: int, payload: TransactionDraftCreate) -> TransactionDraft:
    """
    TODO (when AI integration is added): call speech-to-text here if
    source == "voice", then an LLM to fill parsed_fields/ambiguous_fields
    from raw_input. For now this just stages the raw input with empty
    parsed_fields, status "pending_review", so the rest of the review/
    confirm/reject flow can already be built and tested against it.
    """
    draft = TransactionDraft(
        user_id=user_id,
        slug=generate_slug(db, TransactionDraft, "transaction_draft"),
        source=payload.source,
        raw_input=payload.raw_input,
        parsed_fields={},
        ambiguous_fields={},
        status="pending_review",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def get_draft(db: Session, user_id: int, slug: str) -> TransactionDraft:
    draft = db.execute(select(TransactionDraft).where(TransactionDraft.slug == slug, TransactionDraft.user_id == user_id)).scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


def list_drafts(db: Session, user_id: int, status_filter: str | None = "pending_review") -> list[TransactionDraft]:
    stmt = select(TransactionDraft).where(TransactionDraft.user_id == user_id)
    if status_filter:
        stmt = stmt.where(TransactionDraft.status == status_filter)
    stmt = stmt.order_by(TransactionDraft.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def update_draft_fields(db: Session, user_id: int, slug: str, parsed_fields: dict) -> TransactionDraft:
    """User corrects the AI's guesses before confirming."""
    draft = get_draft(db, user_id, slug)
    if draft.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Draft is '{draft.status}', not editable.")
    # Reject unknown/privileged fields. User may only edit TransactionCreate fields.
    allowed = set(TransactionCreate.model_fields)
    if not set(parsed_fields) <= allowed:
        raise HTTPException(status_code=422, detail="Unsupported draft field.")
    draft.parsed_fields = {**draft.parsed_fields, **parsed_fields}
    db.commit()
    db.refresh(draft)
    return draft


def confirm_draft(db: Session, user_id: int, slug: str):
    """
    Turns a draft into a real transaction. ALWAYS goes through the normal,
    already-validated transaction_service.create_transaction() — a draft
    never writes to the transactions table directly. This is the one
    design rule that must never be violated when the AI parsing is wired
    up: the AI proposes, the existing business-rule engine still decides.
    """
    from app.crud import transaction_service, history_service

    # Lock this draft while confirming: concurrent clicks cannot create duplicates
    # on PostgreSQL. SQLite's no-op FOR UPDATE is sufficient only for local tests.
    draft = db.execute(
        select(TransactionDraft).where(TransactionDraft.slug == slug, TransactionDraft.user_id == user_id).with_for_update()
    ).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status != "pending_review":
        raise HTTPException(status_code=409, detail=f"Draft is '{draft.status}', not confirmable.")
    if draft.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(409, "Draft expired. Parse again before approving.")
    try:
        payload = TransactionCreate.model_validate(draft.parsed_fields)
    except Exception:
        raise HTTPException(status_code=422, detail="Complete all required fields and review any unclear amounts before approval.")
    try:
        # One single DB transaction: posting, history and draft status.
        txn = transaction_service.create_transaction(db, user_id, payload, commit=False)
        history_service.log_entry(
            db, user_id, "transaction", txn.transaction_code, f"{txn.description} - {txn.amount}",
            action="create", after_snapshot=payload.model_dump(mode="json"), performed_by="ai",
        )
        draft.status = "confirmed"
        draft.resulting_transaction_id = txn.id
        db.commit()
        db.refresh(txn)
        return txn
    except Exception:
        db.rollback()
        raise


def reject_draft(db: Session, user_id: int, slug: str) -> None:
    draft = get_draft(db, user_id, slug)
    if draft.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Draft is '{draft.status}', not rejectable.")
    draft.status = "rejected"
    db.commit()



def _lock_bulk_drafts(db: Session, user_id: int, slugs: list[str]) -> dict[str, TransactionDraft]:
    """Validate every requested draft *before* writing anything.

    Lock the user row to serialize bulk operations for that owner, then lock
    all selected draft rows in stable slug order to avoid duplicate approvals.
    On PostgreSQL, these row locks remain held until the final commit.
    """
    from app.models.user import User

    # The user exists through authentication, but this also serializes
    # simultaneous bulk requests for the same user on PostgreSQL.
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
    rows = db.execute(select(TransactionDraft).where(
        TransactionDraft.user_id == user_id,
        TransactionDraft.slug.in_(slugs),
    ).order_by(TransactionDraft.slug).with_for_update()).scalars().all()
    by_slug = {row.slug: row for row in rows}
    for slug in slugs:
        draft = by_slug.get(slug)
        if draft is None:
            raise HTTPException(404, f"Draft not found: {slug}. No changes were saved.")
        if draft.status != "pending_review":
            raise HTTPException(409, f"Draft {slug} is '{draft.status}' and cannot be processed again. No changes were saved.")
    return by_slug


def confirm_drafts_bulk(db: Session, user_id: int, slugs: list[str]) -> BulkDraftConfirmResult:
    """Create individual ordinary transactions, or roll back the ENTIRE batch.

    Do not call confirm_draft(), since it commits per row. Reuse the same
    transaction creation and history services with commit=False instead.
    """
    from app.crud import transaction_service, history_service

    try:
        by_slug = _lock_bulk_drafts(db, user_id, slugs)
        now = datetime.now(timezone.utc)
        # Validate Pydantic structures for ALL rows before any financial write.
        payloads: dict[str, TransactionCreate] = {}
        for slug in slugs:
            draft = by_slug[slug]
            expires = draft.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                raise HTTPException(409, f"Draft {slug} expired. Nothing was posted.")
            try:
                payloads[slug] = TransactionCreate.model_validate(draft.parsed_fields)
            except Exception:
                raise HTTPException(422, f"Draft {slug} has incomplete/invalid fields. Nothing was posted.")

        result = []
        for slug in slugs:
            draft = by_slug[slug]
            payload = payloads[slug]
            try:
                # Identical validated financial engine as manual creation.
                txn = transaction_service.create_transaction(db, user_id, payload, commit=False)
                history_service.log_entry(
                    db, user_id, 'transaction', txn.transaction_code,
                    f"{txn.description} - {txn.amount}",
                    action='create', after_snapshot=payload.model_dump(mode='json'), performed_by='ai',
                )
                draft.status = 'confirmed'
                draft.resulting_transaction_id = txn.id
                result.append(BulkPostedTransaction(draft_slug=slug, transaction_code=txn.transaction_code))
            except HTTPException as error:
                raise HTTPException(error.status_code, f"Draft {slug} failed validation: {error.detail}. Entire batch rolled back.") from error
        # Transaction, history, balance, budget/loan updates and draft flags
        # commit together. This keeps normal Transactions page fully compatible.
        db.commit()
        return BulkDraftConfirmResult(count=len(result), transactions=result)
    except Exception:
        db.rollback()
        raise


def reject_drafts_bulk(db: Session, user_id: int, slugs: list[str]) -> BulkDraftRejectResult:
    """Reject all selected pending drafts as one atomic DB operation."""
    try:
        by_slug = _lock_bulk_drafts(db, user_id, slugs)
        for slug in slugs:
            by_slug[slug].status = 'rejected'
        db.commit()
        return BulkDraftRejectResult(count=len(slugs), rejected_slugs=slugs)
    except Exception:
        db.rollback()
        raise


def purge_expired(db: Session) -> int:
    """Daily cron target — drafts nobody reviewed within 7 days are discarded."""
    now = datetime.now(timezone.utc)
    count = db.query(TransactionDraft).filter(TransactionDraft.expires_at <= now, TransactionDraft.status == "pending_review").update(
        {"status": "expired"}, synchronize_session=False
    )
    db.commit()
    return count
