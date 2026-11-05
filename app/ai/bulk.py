"""LLM *suggestions* only. The AI has no write/database tools.

A reviewed draft must pass the existing transaction engine for a real posting.

This module is the stable public façade of the AI bulk pipeline. The work is
done by:

    shorthand.py  deterministic reading of the user's note shorthand
    context.py    batch-wide details stated anywhere in the note
    planner.py    notes -> plan (entries + AI-suggested items)
    resolve.py    plan + answers -> every field, with its source
    dialog.py     questions, answers and draft rows
    session.py    the stored conversation (analyze -> answer -> finalize)

The one-shot helpers below (`preflight_bulk`, `stage_bulk`) keep the original
API working: same inputs, same outputs, now sharing the new engine.
"""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import shorthand as sh
from app.ai.context import llm_context, rules_context
from app.ai.dialog import build_draft_rows, legacy_common
from app.ai.extract import extract_chunk
from app.ai.matching import find_mentions
from app.ai.options import available_options
from app.ai.planner import CHUNK, DEFAULT_LIMIT, build_plan, prepare_batch  # noqa: F401
from app.ai.resolve import draft_notes, empty_answers, resolve_item
from app.models.transaction_draft import TransactionDraft
from app.schemas.ai_bulk import BulkAnswers, Candidate
from app.utils.slugs import generate_slug

# Kept for any code importing the old private names.
_NAMED_DATE = sh.NAMED_DATE
_NUMERIC_DATE = sh.NUMERIC_DATE
_QUANTITY_PRICE = sh.QUANTITY_PRICE


def _has_date_in_segment(text: str) -> bool:
    return bool(sh.NAMED_DATE.search(text) or sh.NUMERIC_DATE.search(text))


def prepare_bulk_lines(raw_text):
    """Per-draft text, exact source provenance and explicit date scope."""
    prepared = prepare_batch(raw_text, {}, today=date.today())
    return [{'id': e['id'], 'text': e['text'], 'source_line': e['source_line'], 'source_part': e['source_part'],
             'header_date': date.fromisoformat(e['header_date']) if e['header_date'] else None,
             'header_partial': e['header_partial']} for e in prepared['entries']]


def prepare_with_global_context(raw_text: str, options: dict):
    """Apply batch instructions regardless of where the user put them."""
    prepared = prepare_batch(raw_text, options, today=date.today())
    ctx = rules_context(prepared['instruction_lines'], options)
    rows = [{'id': e['id'], 'text': e['text'], 'source_line': e['source_line'], 'source_part': e['source_part'],
             'header_date': date.fromisoformat(e['header_date']) if e['header_date'] else None,
             'header_partial': e['header_partial']} for e in prepared['entries']]
    common = {'account_slug': ctx['account_slug'], 'transaction_type_slug': ctx['transaction_type_slug'],
              'category_slug': ctx['category_slug']}
    return rows, common


def chunks_from_text(raw_text):
    """Compatibility helper: numbered extraction items, not physical lines."""
    return [(row['id'], row['text']) for row in prepare_bulk_lines(raw_text)]


def preflight_bulk(db: Session, user_id: int, raw_text: str) -> dict:
    """Cheap, deterministic first step. Does not call LLM or write to database.

    Legacy shape. The conversational flow (session.analyze) supersedes this, but
    the endpoint and its callers keep working.
    """
    import re
    options = available_options(db, user_id)
    prepared = prepare_batch(raw_text, options, today=date.today())
    entries = prepared['entries']
    ctx = rules_context(prepared['instruction_lines'], options)
    missing_dates = [e for e in entries if not e['header_date'] and not _has_date_in_segment(e['text'])]
    missing_accounts = [e for e in entries
                        if not find_mentions(e['text'], options['accounts']) and not find_mentions(e['text'], options['payment_methods'])]
    questions = []
    if missing_dates:
        partials = {e['header_partial'] for e in missing_dates}
        questions.append({
            'field': 'txn_date', 'required': True,
            'question': (f'Notes mein {next(iter(partials))} (day-month) mila hai, lekin year missing hai. Puri date confirm karein.'
                         if len(partials) == 1 and None not in partials
                         else f'{len(missing_dates)} entries ki date clear nahi. In sab ki date kya hai? Aaj ki date chahiye to aaj select karein.'),
            'choices': [], 'affected_count': len(missing_dates),
        })
    if missing_accounts and not ctx['account_slug']:
        if not options['accounts']:
            raise HTTPException(422, 'You must create an active financial account before importing transactions.')
        questions.append({
            'field': 'account_slug', 'required': True,
            'question': f'{len(missing_accounts)} entries mein account clear nahi. Un entries ke liye kaunsa account use karein?',
            'choices': options['accounts'], 'affected_count': len(missing_accounts),
        })
    amount_only = [e for e in entries if re.fullmatch(r'(?i)\s*(?:rs\.?|pkr)?\s*\d[\d,.]*\s*', e['text'])]
    if amount_only:
        questions.extend([
            {'field': 'transaction_type_slug', 'required': False,
             'question': 'Kuch amounts mein transaction type nahi batayi gayi. Kya un sab ke liye koi common type hai? (Mixed hon to skip karein)',
             'choices': options['types'], 'affected_count': len(amount_only)},
            {'field': 'category_slug', 'required': False,
             'question': 'Kya un unclear entries ke liye koi common category hai? (Different categories hon to skip karein)',
             'choices': options['categories'], 'affected_count': len(amount_only)},
        ])
    same_date = bool(entries) and all(e['header_date'] == entries[0]['header_date'] for e in entries) and entries[0]['header_date']
    same_partial = bool(entries) and entries[0]['header_partial'] and all(e['header_partial'] == entries[0]['header_partial'] for e in entries)
    return {
        'entry_count': len(entries), 'questions': questions,
        'detected': {'global_date': entries[0]['header_date'] if same_date else None,
                     'global_date_without_year': entries[0]['header_partial'] if same_partial else None,
                     'global_account_slug': ctx['account_slug'],
                     'global_payment_method_slug': ctx['payment_method_slug'],
                     'global_priority_slug': ctx['priority_slug'],
                     'global_type_slug': ctx['transaction_type_slug'],
                     'global_category_slug': ctx['category_slug']},
        'note': 'Common details are reviewed here. Individual uncertain types, categories, loan details and amounts must still be confirmed in each draft.',
    }


def validate_bulk_answers(answers: BulkAnswers, options: dict) -> None:
    for field, key in (('account_slug', 'accounts'), ('payment_method_slug', 'payment_methods'),
                       ('priority_slug', 'priorities'), ('transaction_type_slug', 'types'),
                       ('category_slug', 'categories'), ('contact_slug', 'contacts')):
        value = getattr(answers, field)
        if value and not any(x['slug'] == value for x in options[key]):
            raise HTTPException(422, f'Invalid {field}. Choose an active option from your own account.')
    if answers.category_slug and answers.transaction_type_slug:
        category = next(x for x in options['categories'] if x['slug'] == answers.category_slug)
        if answers.transaction_type_slug not in category['type_slugs']:
            raise HTTPException(422, 'Selected category does not support the selected transaction type.')


def candidate_to_fields(candidate, text, options, *, today, header_date=None):
    """Fields + warnings for ONE candidate. Do not invent missing fields."""
    entry = {'id': 1, 'text': text, 'raw': text, 'source_line': 1, 'source_part': 1,
             'header_date': header_date.isoformat() if header_date else None, 'header_partial': None,
             'hint': None, 'chain': False}
    plan = {'entries': [entry], 'items': [{'id': 1, 'entry_id': 1, 'cand': Candidate.model_validate(candidate).model_dump()}],
            'context': rules_context([], options)}
    r = resolve_item(plan, plan['items'][0], entry, options, empty_answers(), today=today)
    if not r['fields']['txn_date']:
        r['fields']['txn_date'] = today.isoformat()
        r['warnings'].append('No date supplied; defaulted to today. Please verify the date.')
    return r['fields'], {'warnings': draft_notes(r)}


def _store_rows(db: Session, user_id: int, rows: list[dict], source: str) -> list[TransactionDraft]:
    if len(rows) > 400:
        raise HTTPException(422, 'Too many proposed transactions. Split the input into smaller batches.')
    staged = []
    for row in rows:
        draft = TransactionDraft(user_id=user_id, slug=generate_slug(db, TransactionDraft, 'transaction_draft'),
                                 source=source, raw_input=row['raw_input'], parsed_fields=row['fields'],
                                 ambiguous_fields=row['ambiguous'], status='pending_review')
        db.add(draft)
        staged.append(draft)
    db.commit()
    for item in staged:
        db.refresh(item)
    return staged


def stage_bulk(db: Session, user_id: int, raw_text: str, source: str = 'bulk_text', extractor=extract_chunk,
               answers: BulkAnswers | None = None, context_llm=None):
    """One-shot: analyse the notes and create drafts at once (no questions asked).

    Missing details are left blank and flagged in each draft. Prefer the
    conversational flow (session.analyze / answer / finalize).
    """
    options = available_options(db, user_id)
    answers = answers or BulkAnswers()
    validate_bulk_answers(answers, options)
    from app.ai.providers import provider_name
    try:
        limit = 1 if provider_name() in ('groq', 'ollama') else 2
    except HTTPException:
        limit = 1
    today = date.today()
    plan = build_plan(options, raw_text, extractor=extractor, context_llm=context_llm, today=today,
                      fallback_date=answers.txn_date, provider_limit=limit)
    stored = empty_answers()
    stored['common'] = legacy_common(answers)
    rows = build_draft_rows(plan, stored, options, today=today)
    return _store_rows(db, user_id, rows, source)


def page_drafts(db: Session, user_id: int, page: int, page_size: int):
    from sqlalchemy import func
    base = select(TransactionDraft).where(TransactionDraft.user_id == user_id, TransactionDraft.status == 'pending_review')
    total = db.scalar(select(func.count()).select_from(TransactionDraft).where(TransactionDraft.user_id == user_id, TransactionDraft.status == 'pending_review'))
    rows = db.execute(base.order_by(TransactionDraft.created_at.desc(), TransactionDraft.id.desc())
                      .offset((page-1)*page_size).limit(page_size)).scalars().all()
    return {'items': rows, 'total': total or 0, 'page': page, 'page_size': page_size}
