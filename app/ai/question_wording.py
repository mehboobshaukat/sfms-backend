"""Optional question wording helper; no HTML, controls or posting rights for the LLM."""
from __future__ import annotations
import json
import logging
import re
from app.core.config import settings
from app.schemas.ai_bulk import SuggestedQuestionBatch
logger = logging.getLogger(__name__)


def question_rewrites(plan: dict, questions: list[dict]) -> dict[str, dict]:
    if not getattr(settings, 'AI_REPHRASE_QUESTIONS', False) or not questions:
        return {}
    shortlist = questions[:12]  # One bounded optional call; no loops.
    payload = [{'id': q['id'], 'field': q['field'], 'scope': q['scope'],
                'affected_count': q['affected_count'], 'question': q['question']}
               for q in shortlist]
    lang = plan.get('reply_language', 'en')
    system = (
        'You edit user-friendly finance clarification question wording only. '
        'Return strictly schema-valid JSON. You MUST use only the given question IDs. '
        'Preserve exactly the missing financial information and question meaning; '
        'do not invent missing amounts, dates, categories, accounts, contacts, or answers. '
        'English by default, Roman Urdu only for Roman Urdu input. '
        'Output plain text: never HTML, Markdown, code, Javascript, or CSS. '
        'A group question must explicitly refer to the number of affected entries. '
        'Do not ask for optional fields as mandatory. Keep each question under 180 chars.'
    )
    try:
        from app.ai.providers import call_structured
        result = call_structured(system, json.dumps({'language': lang, 'questions':payload}),
                                 SuggestedQuestionBatch, 'sfms_question_wording')
    except Exception as exc:
        logger.warning('AI question wording unavailable (%s); using safe backend questions', type(exc).__name__)
        return {}
    allowed = {q['id'] for q in shortlist}
    out = {}
    for q in result.questions:
        value = q.question.strip()
        if q.id not in allowed or '<' in value or '>' in value or '{{' in value or len(value) > 180:
            continue
        hint = (q.hint or '').strip()
        if '<' in hint or '>' in hint or len(hint) > 220:
            hint = ''
        out[q.id] = {'question': value, 'hint': hint or None}
    return out
