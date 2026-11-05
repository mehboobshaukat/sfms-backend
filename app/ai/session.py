"""The stored conversation: analyze -> answer (repeat) -> finalize.

    analyze   reads the notes ONCE (AI), stores the plan, returns the questions
    answer    applies the user's answers locally (no AI), returns what is left
    finalize  turns the plan + answers into reviewable drafts

Nothing here posts a transaction: drafts still need the user's own approval
through the existing draft confirm endpoint.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.bulk import _store_rows
from app.ai.context import llm_context
from app.ai.dialog import apply_answers, build_draft_rows, plan_view, compute_questions
from app.ai.extract import extract_chunk
from app.ai.options import available_options
from app.ai.planner import build_plan
from app.ai.resolve import empty_answers
from app.ai.question_wording import question_rewrites
from app.models.ai_bulk_session import AiBulkSession
from app.schemas.ai_bulk import AnswerItem
from app.utils.slugs import generate_slug


def _provider_limit() -> int:
    from app.ai.providers import provider_name
    try:
        return 1 if provider_name() in ('groq', 'ollama') else 2
    except HTTPException:
        return 1


def _load(db: Session, user_id: int, slug: str, *, for_update: bool = False) -> AiBulkSession:
    stmt = select(AiBulkSession).where(AiBulkSession.slug == slug, AiBulkSession.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(404, 'AI import session not found.')
    if session.status == 'awaiting_answers' and session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        session.status = 'expired'
        db.commit()
    return session


def _view(session: AiBulkSession, options: dict, today: date | None = None) -> dict:
    return plan_view(session.plan, session.answers, options, today=today or date.today(),
                     session_slug=session.slug, status=session.status)


def analyze(db: Session, user_id: int, raw_text: str, source: str = 'bulk_text', *,
            extractor=extract_chunk, context_llm=llm_context, today: date | None = None) -> dict:
    options = available_options(db, user_id)
    if not options['accounts']:
        raise HTTPException(422, 'You must create an active financial account before importing transactions.')
    today = today or date.today()
    plan = build_plan(options, raw_text, extractor=extractor, context_llm=context_llm, today=today,
                      provider_limit=_provider_limit())
    # Optional, single bounded AI call only to improve wording of questions;
    # fields, selectable options, and financial rules remain backend-owned.
    proposed_questions, _ = compute_questions(plan, empty_answers(), options, today=today)
    plan['question_rewrites'] = question_rewrites(plan, proposed_questions)
    # Housekeeping: this user's abandoned sessions older than their expiry.
    db.query(AiBulkSession).filter(AiBulkSession.user_id == user_id,
                                   AiBulkSession.expires_at <= datetime.now(timezone.utc)).delete(synchronize_session=False)
    session = AiBulkSession(user_id=user_id, slug=generate_slug(db, AiBulkSession, 'ai_bulk_session'),
                            source=source, raw_text=raw_text, plan=plan, answers=empty_answers(),
                            status='awaiting_answers')
    db.add(session)
    db.commit()
    db.refresh(session)
    return _view(session, options, today)


def get_view(db: Session, user_id: int, slug: str) -> dict:
    session = _load(db, user_id, slug)
    return _view(session, available_options(db, user_id))


def answer(db: Session, user_id: int, slug: str, payload: list[AnswerItem]) -> dict:
    session = _load(db, user_id, slug, for_update=True)
    if session.status != 'awaiting_answers':
        raise HTTPException(409, f"This import is '{session.status}'. Start a new import.")
    options = available_options(db, user_id)
    session.answers = apply_answers(session.plan, session.answers, payload, options)
    db.commit()
    db.refresh(session)
    return _view(session, options)


def finalize(db: Session, user_id: int, slug: str) -> list:
    session = _load(db, user_id, slug, for_update=True)
    if session.status != 'awaiting_answers':
        raise HTTPException(409, f"This import is '{session.status}'; drafts were already created or it expired.")
    options = available_options(db, user_id)
    rows = build_draft_rows(session.plan, session.answers, options, today=date.today())
    if not rows:
        raise HTTPException(422, 'Nothing left to create. All items were skipped.')
    session.status = 'finalized'
    return _store_rows(db, user_id, rows, session.source)


def cancel(db: Session, user_id: int, slug: str) -> None:
    session = _load(db, user_id, slug, for_update=True)
    if session.status == 'awaiting_answers':
        session.status = 'cancelled'
        db.commit()


def purge_expired_sessions(db: Session) -> int:
    """Daily cron target: sessions nobody finished within 24 hours are deleted."""
    count = db.query(AiBulkSession).filter(AiBulkSession.expires_at <= datetime.now(timezone.utc)).delete(synchronize_session=False)
    db.commit()
    return count
