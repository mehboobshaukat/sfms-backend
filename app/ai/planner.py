"""Step 1 of the conversational flow: read the whole note and build a PLAN.

    raw note --prepare--> entries + instruction lines
             --context--> batch-wide details the user stated anywhere
             --LLM------> suggested items (one or more per entry)

The plan is plain JSON. It is stored with the session so that the user's
answers can be applied deterministically (no second AI call): every question
the assistant asks and every draft it creates is computed from plan + answers
by resolve.py / dialog.py.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import logging
import re

from fastapi import HTTPException

from app.ai import shorthand as sh
from app.ai.locale import reply_language
from app.ai.input_security import validate_transaction_note
from app.ai.context import build_context, llm_context
from app.ai.extract import extract_chunk, rule_candidate
from app.schemas.ai_bulk import Candidate

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
CHUNK = 20
MAX_ITEMS = 400


def _option_names(options: dict) -> list[str]:
    return [x['name'] for key in ('accounts', 'payment_methods', 'priorities', 'types', 'categories')
            for x in options.get(key, []) if x.get('name')]


def prepare_batch(raw_text: str, options: dict, *, today: date) -> dict:
    """Split the note into entries and instruction lines.

    Heading dates apply to the entries that follow them. A single distinct date
    anywhere in the note applies to every entry, even those written before it
    (users often write "yeh 10-September-2026 ki transactions hain" last).
    """
    validate_transaction_note(raw_text)
    names = _option_names(options)
    entries: list[dict] = []
    instruction_lines: list[str] = []
    active_date: date | None = None
    active_partial: str | None = None
    dates_seen: list[date] = []
    partials_seen: list[str] = []
    for source_line, line in enumerate(raw_text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if sh.is_instruction_line(line, names):
            instruction_lines.append(line)
            explicit = sh.parse_header_date(line)
            partial = None if explicit else sh.parse_partial_date(line)
            if explicit is None and partial is None:
                if sh.TODAY_WORD.search(line):
                    explicit = today
                elif sh.YESTERDAY_WORD.search(line):
                    explicit = today - timedelta(days=1)
            if explicit is not None:
                active_date, active_partial = explicit, None
                dates_seen.append(explicit)
            elif partial is not None:
                active_date, active_partial = None, partial
                partials_seen.append(partial)
            continue
        split = sh.split_line(line)
        instruction_lines.extend(split['inline_instructions'])
        for part_index, part in enumerate(split['parts'], 1):
            entries.append({
                'id': len(entries) + 1, 'text': part['text'], 'raw': part['raw'],
                'source_line': source_line, 'source_part': part_index,
                'header_date': active_date.isoformat() if active_date else None,
                'header_partial': active_partial,
                'hint': part['hint'], 'chain': part['chain'],
            })
    if not entries:
        raise HTTPException(422, "Please enter at least one transaction (not just a date heading).")
    if len(entries) > DEFAULT_LIMIT:
        raise HTTPException(422, f"Maximum {DEFAULT_LIMIT} transaction entries per import. Split a larger import into batches.")
    if len({d for d in dates_seen}) == 1:
        for e in entries:
            if e['header_date'] is None and not e['header_partial']:
                e['header_date'] = dates_seen[0].isoformat()
    if len(set(partials_seen)) == 1:
        for e in entries:
            if e['header_date'] is None and not e['header_partial']:
                e['header_partial'] = partials_seen[0]
    return {'entries': entries, 'instruction_lines': instruction_lines}


def _effective_date(entry: dict, fallback: date | None, today: date) -> date:
    if entry['header_date']:
        return date.fromisoformat(entry['header_date'])
    return fallback or today


def _run_extraction(extractor, options, entries, *, today, fallback_date, provider_limit, full_text=None):
    """Chunk entries by date scope and extract; a failing chunk degrades, never aborts."""
    batches: list[tuple[date, list[tuple[int, str]]]] = []
    for e in entries:
        eff = _effective_date(e, fallback_date, today)
        if not batches or batches[-1][0] != eff or len(batches[-1][1]) >= CHUNK:
            batches.append((eff, []))
        batches[-1][1].append((e['id'], e['text']))

    def run(batch_date, batch):
        try:
            # External injectors used by tests keep the original signature.
            if extractor is extract_chunk:
                extracted = extractor(batch, options, today=batch_date, full_text=full_text)
            else:
                extracted = extractor(batch, options, today=batch_date)
            return batch, extracted, None
        except HTTPException as exc:
            return batch, None, ("AI service rate limit reached; basic extraction used and review is required." if exc.status_code == 429 else "Some entries need manual review because automatic extraction was unavailable.")
        except Exception:
            logger.exception("AI extraction failed for a chunk")
            return batch, None, "AI parsing failed for part of the notes."

    workers = max(1, min(provider_limit, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, d, b) for d, b in batches]
        return [f.result() for f in futures]


def build_plan(options: dict, raw_text: str, *, extractor=extract_chunk, context_llm=llm_context,
               today: date | None = None, fallback_date: date | None = None,
               provider_limit: int = 2) -> dict:
    today = today or date.today()
    prepared = prepare_batch(raw_text, options, today=today)
    entries = prepared['entries']
    ctx = build_context(prepared['instruction_lines'], options, llm=context_llm)
    # A date understood only by the AI step (unusual phrasing) fills entries
    # that have no heading date.
    ctx_date = ctx.get('txn_date')
    if not ctx_date and ctx.get('is_today'):
        ctx_date = today.isoformat()
    for e in entries:
        if e['header_date'] is None and not e['header_partial']:
            if ctx_date:
                e['header_date'] = ctx_date
            elif ctx.get('txn_date_day_month'):
                e['header_partial'] = ctx['txn_date_day_month']

    degraded_reasons: list[str] = []
    if ctx.get('degraded_reason'):
        degraded_reasons.append(ctx['degraded_reason'])
    results = _run_extraction(extractor, options, entries, today=today, fallback_date=fallback_date,
                              provider_limit=provider_limit, full_text=raw_text)
    by_entry: dict[int, list[Candidate]] = {e['id']: [] for e in entries}
    failed_ids: set[int] = set()
    for batch, candidates, error in results:
        if error is not None:
            degraded_reasons.append(error)
            failed_ids.update(i for i, _ in batch)
            continue
        for cand in candidates:
            if cand.source_line in by_entry:
                by_entry[cand.source_line].append(cand)
            else:
                degraded_reasons.append('AI returned an unknown segment reference; it was ignored.')
    items: list[dict] = []
    for e in entries:
        cands = by_entry[e['id']]
        if not cands:
            reason = ('AI is unavailable right now; this draft was read with basic rules. Please verify.'
                      if e['id'] in failed_ids else
                      'AI did not recognize this entry; read with basic rules. Please verify.')
            cands = [rule_candidate(e['id'], e['text'], reason)]
        for cand in cands:
            items.append({'id': len(items) + 1, 'entry_id': e['id'], 'cand': cand.model_dump()})
    if len(items) > MAX_ITEMS:
        raise HTTPException(422, 'Too many proposed transactions. Split the input into smaller batches.')
    unique_reasons = list(dict.fromkeys(degraded_reasons))
    return {
        'version': 2, 'today': today.isoformat(), 'reply_language': reply_language(raw_text), 'entries': entries, 'items': items,
        'instruction_lines': prepared['instruction_lines'], 'context': ctx,
        'degraded': bool(unique_reasons), 'degraded_reasons': unique_reasons[:3],
        'currency_code': options.get('currency_code', 'PKR'),
    }
