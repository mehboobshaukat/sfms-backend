"""AI text -> reviewable draft suggestions. No endpoint here posts a transaction.

Conversational flow (preferred):
    POST   /ai/bulk/analyze                    read the notes once, get questions
    POST   /ai/bulk/sessions/{slug}/answers    answer them (no AI call), get what is left
    POST   /ai/bulk/sessions/{slug}/finalize   create the reviewable drafts
    GET    /ai/bulk/sessions/{slug}            resume after a page refresh
    DELETE /ai/bulk/sessions/{slug}            cancel

One-shot flow (kept for compatibility): POST /ai/bulk/preflight, POST /ai/bulk/parse.
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.ai_bulk import AnalyzeRequest, AnswersRequest, BulkParseRequest, BulkPreflightRequest
from app.schemas.transaction_draft import TransactionDraftOut
from app.ai import session as ai_session
from app.ai.input_security import validate_transaction_note
from app.ai.bulk import stage_bulk, preflight_bulk, available_options, page_drafts
from app.ai.context import llm_context
from app.ai.providers import public_provider_info

router = APIRouter(prefix='/ai/bulk', tags=['AI Bulk Transaction Review'])


def _drafts_out(rows):
    return {'count': len(rows), 'drafts': [TransactionDraftOut.model_validate(x).model_dump(mode='json') for x in rows]}


def _guarded(db: Session, fn):
    """Run fn(); never leave a half-written session and never leak internals."""
    try:
        return fn()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, 'Could not prepare drafts. No transactions were posted.')


@router.get('/options')
def options(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {**available_options(db, current_user.id), "ai_provider": public_provider_info()}

@router.get('/drafts')
def drafts(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = page_drafts(db, current_user.id, page, page_size)
    result['items'] = [TransactionDraftOut.model_validate(x).model_dump(mode='json') for x in result['items']]
    return result

@router.post('/preflight')
@limiter.limit('12/minute')
def preflight(request: Request, payload: BulkPreflightRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    validate_transaction_note(payload.raw_text)
    return preflight_bulk(db, current_user.id, payload.raw_text)

@router.post('/parse')
@limiter.limit('3/minute')
def parse(request: Request, payload: BulkParseRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    validate_transaction_note(payload.raw_text)
    return _guarded(db, lambda: _drafts_out(stage_bulk(
        db, current_user.id, payload.raw_text, source=payload.source, answers=payload.answers,
        context_llm=llm_context)))


@router.post('/analyze')
@limiter.limit('5/minute')
def analyze(request: Request, payload: AnalyzeRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    validate_transaction_note(payload.raw_text)
    return _guarded(db, lambda: ai_session.analyze(db, current_user.id, payload.raw_text, payload.source))

@router.get('/sessions/{slug}')
def session_state(slug: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _guarded(db, lambda: ai_session.get_view(db, current_user.id, slug))

@router.post('/sessions/{slug}/answers')
@limiter.limit('60/minute')
def session_answers(request: Request, slug: str, payload: AnswersRequest,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _guarded(db, lambda: ai_session.answer(db, current_user.id, slug, payload.answers))

@router.post('/sessions/{slug}/finalize')
@limiter.limit('20/minute')
def session_finalize(request: Request, slug: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _guarded(db, lambda: _drafts_out(ai_session.finalize(db, current_user.id, slug)))

@router.delete('/sessions/{slug}', status_code=204)
def session_cancel(slug: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _guarded(db, lambda: ai_session.cancel(db, current_user.id, slug))
