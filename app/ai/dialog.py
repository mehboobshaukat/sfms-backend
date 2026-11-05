"""Step 3: the conversation. Questions are COMPUTED from plan + answers.

Nothing here calls the AI. After each round of answers the questions are
recomputed, so a later answer can unlock a dependent question (choose the
type -> then its category; choose the account -> then its payment method).
Every (field, item) is asked at most once ('asked'), so the loop always ends.

Common problems are asked ONCE for the whole group ("5 entries: which
account?"); the user may instead pick "alag alag" and be asked per entry.
"""
from __future__ import annotations

from datetime import date
import re

from fastapi import HTTPException

from app.ai.resolve import draft_notes, normalize_answers, resolve_all
from app.ai.locale import choose

OPTIONAL_FIELDS = {'txn_time', 'priority_slug'}
EACH_FIELDS = {'txn_date', 'account_slug', 'payment_method_slug', 'priority_slug',
               'transaction_type_slug', 'category_slug'}
GROUPED_ORDER = ('txn_date', 'account_slug', 'payment_method_slug', 'priority_slug',
                 'transaction_type_slug', 'category_slug')
ITEM_ONLY_ORDER = ('amount', 'contact_slug', 'loan_code', 'credit_purchase_slug', 'from_account_slug', 'to_account_slug', 'include')
ANSWER_FIELDS = set(GROUPED_ORDER) | set(ITEM_ONLY_ORDER) | {'txn_time', 'convention'}
CONVENTIONS = ('plus_outflow', 'plus_inflow', 'ignore')
MAX_ITEM_QUESTIONS = 40
PREVIEW = 5
_OPTION_KEYS = {'loan_code': 'loans', 'credit_purchase_slug': 'credit_purchases',
                'from_account_slug': 'accounts', 'to_account_slug': 'accounts', 'account_slug': 'accounts', 'priority_slug': 'priorities', 'transaction_type_slug': 'types',
                'category_slug': 'categories', 'contact_slug': 'contacts', 'payment_method_slug': 'payment_methods'}


# ---- applying answers --------------------------------------------------------

def validate_answer_value(field: str, value: str | None, options: dict) -> str:
    if value is None or not str(value).strip():
        raise HTTPException(422, f'Please choose a value for {field}.')
    value = str(value).strip()
    if field == 'txn_date':
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, 'Invalid date.') from None
        if not (2000 <= parsed.year <= 2100):
            raise HTTPException(422, 'Date year is out of range.')
        return parsed.isoformat()
    if field == 'txn_time':
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
            raise HTTPException(422, 'Time must be HH:MM (24-hour).')
        return value
    if field == 'amount':
        from decimal import Decimal, InvalidOperation
        try:
            amount = Decimal(value.replace(',', ''))
        except InvalidOperation:
            raise HTTPException(422, 'Invalid amount.') from None
        if not amount.is_finite() or amount <= 0:
            raise HTTPException(422, 'Amount must be greater than zero.')
        return str(amount)
    if field == 'include':
        if value not in ('yes', 'no'):
            raise HTTPException(422, 'Invalid choice.')
        return value
    if field in _OPTION_KEYS:
        if not any(x['slug'] == value for x in options[_OPTION_KEYS[field]]):
            raise HTTPException(422, f'Invalid {field}. Choose an active option from your own account.')
        return value
    raise HTTPException(422, 'Unsupported answer field.')


def apply_answers(plan: dict, answers: dict, payload, options: dict) -> dict:
    """Merge a list of AnswerItem into the stored answers (returns a new dict)."""
    answers = normalize_answers(answers)
    answers = {**answers, 'items': {k: dict(v) for k, v in answers['items'].items()},
               'each': {k: list(v) for k, v in answers['each'].items()},
               'asked': {k: list(v) for k, v in answers['asked'].items()}}
    valid = {i['id'] for i in plan['items']}
    for a in payload:
        if a.field not in ANSWER_FIELDS:
            raise HTTPException(422, 'Unsupported answer field.')
        if a.field == 'convention':
            if a.value not in CONVENTIONS:
                raise HTTPException(422, 'Invalid pattern choice.')
            answers['convention'] = a.value
            continue
        ids = list(dict.fromkeys(a.item_ids)) or sorted(valid)
        if any(i not in valid for i in ids):
            raise HTTPException(422, 'Answer refers to an unknown draft item.')
        if a.mode == 'each':
            answers['each'][a.field] = sorted(set(answers['each'].get(a.field, [])) | set(ids))
            continue
        asked = set(answers['asked'].get(a.field, [])) | set(ids)
        answers['asked'][a.field] = sorted(asked)
        if a.mode == 'skip':
            continue
        value = validate_answer_value(a.field, a.value, options)
        for i in ids:
            answers['items'].setdefault(str(i), {})[a.field] = value
    return answers


def legacy_common(answers_model) -> dict:
    """BulkAnswers (flat, whole-batch defaults) -> stored 'common' answers."""
    out = {}
    for key, value in answers_model.model_dump().items():
        if value is not None:
            out[key] = value.isoformat() if hasattr(value, 'isoformat') else value
    return out


# ---- computing questions --------------------------------------------------------

def _clip(text: str, n: int = 110) -> str:
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + '…'


def _brief(plan: dict, r: dict) -> dict:
    item = next(i for i in plan['items'] if i['id'] == r['item_id'])
    entry = next(e for e in plan['entries'] if e['id'] == r['entry_id'])
    return {'id': r['item_id'], 'text': _clip(entry['raw']), 'description': _clip(r['fields']['description'], 60),
            'amount': r['fields']['amount'] or item['cand'].get('amount')}


def _choices(items: list[dict]) -> list[dict]:
    return [{'slug': x['slug'], 'name': x['name']} for x in items]


def _make(plan, field, rows, *, scope, group_key, question, input_kind='select', choices=None,
          suggested=None, hint=None, required=None):
    ids = [r['item_id'] for r in rows]
    return {
        'id': f"{field}|{group_key}", 'field': field, 'scope': scope,
        'required': (field not in OPTIONAL_FIELDS) if required is None else required,
        'input': input_kind, 'question': question, 'hint': hint,
        'choices': choices or [], 'suggested': suggested,
        'item_ids': ids, 'items': [_brief(plan, r) for r in rows[:PREVIEW]], 'affected_count': len(rows),
        'allow_each': scope == 'group' and field in EACH_FIELDS and len(rows) > 1,
        'allow_skip': True,
    }


def _group_question(plan, options, field, group_key, rows):
    n = len(rows)
    first = rows[0]
    if field == 'txn_date':
        partials = {r['info'].get('partial_date') for r in rows}
        if len(partials) == 1 and None not in partials:
            q = (choose(plan, f'The notes contain {next(iter(partials))} (day-month), but no year. Please confirm the full date.', f'Notes mein {next(iter(partials))} (day-month) mila hai, lekin saal missing hai. Poori date confirm karein.'))
        else:
            q = choose(plan, f'The date is missing for {n} entries. What date should I use?', f'{n} entries ki date clear nahi. In sab ki date kya hai?')
        return _make(plan, field, rows, scope='group', group_key=group_key, question=q, input_kind='date')
    if field == 'account_slug':
        return _make(plan, field, rows, scope='group', group_key=group_key,
                     question=choose(plan, f'Which account should I use for {n} entries?', f'{n} entries ka account clear nahi. In ke liye kaunsa account use karein?'),
                     choices=_choices(options['accounts']),
                     suggested=next((a['slug'] for a in options['accounts'] if a.get('is_default')), None))
    if field == 'payment_method_slug':
        account = options_account(options, first['account'])
        methods = [m for m in options['payment_methods'] if m['account_slug'] == first['account']]
        return _make(plan, field, rows, scope='group', group_key=group_key,
                     question=choose(plan, f"Which payment method applies to {n} entries in {account['name']}?", f"{account['name']} account ki {n} entries ka payment method kaunsa hai?"),
                     choices=_choices(methods))
    if field == 'priority_slug':
        return _make(plan, field, rows, scope='group', group_key=group_key,
                     question=choose(plan, f'What priority should I use for {n} entries?', f'{n} entries ki priority kya rakhein?'), choices=_choices(options['priorities']),
                     suggested=next((p['slug'] for p in options['priorities'] if p.get('is_default')), None))
    if field == 'transaction_type_slug':
        direction = first['info'].get('direction')
        pool = [t for t in options['types'] if direction in ('inflow', 'outflow', 'transfer') and t['nature'] == direction] \
            or options['types']
        label = {'outflow': 'paisa jaane (outflow)', 'inflow': 'paisa aane (inflow)', 'transfer': 'transfer'}.get(direction)
        q = choose(plan, (f'What transaction type applies to these {n} {direction or "unclassified"} entries?' if label else
                      f'What transaction type should I use for these {n} entries?'),
               (f'{n} entries {label} wali lagti hain. Inka transaction type kaunsa hai?' if label else
                f'{n} entries ka transaction type clear nahi. Kaunsa type rakhein?'))
        if direction == 'inflow':
            q += choose(plan, ' (Money received is not automatically income or a loan; choose its purpose.)', ' (Kisi ka diya hua paisa khud-ba-khud income ya loan nahi hota; sahi type chunein.)')
        return _make(plan, field, rows, scope='group', group_key=group_key, question=q, choices=_choices(pool))
    if field == 'category_slug':
        type_slug = first['fields']['transaction_type_slug']
        type_info = next(t for t in options['types'] if t['slug'] == type_slug)
        pool = [c for c in options['categories'] if type_slug in c['type_slugs']]
        return _make(plan, field, rows, scope='group', group_key=group_key,
                     question=choose(plan, f"Which category applies to {n} {type_info['name']} entries?", f"{type_info['name']} type ki {n} entries ki category kaunsi hai?"), choices=_choices(pool))
    raise AssertionError(field)


def options_account(options: dict, slug: str) -> dict:
    return next(a for a in options['accounts'] if a['slug'] == slug)


def _item_question(plan, options, field, r):
    desc = r['fields']['description']
    amount = r['fields']['amount']
    label = f'"{_clip(desc, 50)}"' + (f' ({amount})' if amount else '')
    if field == 'amount':
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'What is the exact amount for {label}?', f'{label} ka exact amount kitna hai?'), input_kind='number',
                     hint='; '.join(w for w in r['warnings'] if 'mount' in w or 'Quantity' in w) or None)
    if field == 'contact_slug':
        pool = options['contacts']
        if r['info'].get('contact_choices') is not None:
            pool = [c for c in pool if c['slug'] in r['info']['contact_choices']]
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'Which contact is associated with the loan {label}?', f'{label} loan hai. Kis contact ke saath?'), choices=_choices(pool))
    if field == 'loan_code':
        pool = [l for l in options.get('loans', []) if l['loan_type'] == ('received' if r['type_name'] == 'loan repaid' else 'given')
                and (not r['fields'].get('contact_slug') or l['contact_slug'] == r['fields']['contact_slug'])]
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=f'Which existing loan does {label} settle?', choices=_choices(pool))
    if field == 'credit_purchase_slug':
        pool = [x for x in options.get('credit_purchases', []) if x['credit_type'] == ('received' if r['type_name'] == 'item payment made' else 'given')]
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=f'Which existing credit purchase does {label} settle?', choices=_choices(pool))
    if field in ('from_account_slug', 'to_account_slug'):
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=f"Select the {'source' if field == 'from_account_slug' else 'destination'} account for {label}.",
                     choices=_choices(options['accounts']))
    if field == 'include':
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'A separate amount appeared inside {label}. Should I create another draft?', f'Entry ke andar {label} ka alag amount mila. Isay alag transaction banayein?'),
                     choices=[{'slug': 'yes', 'name': choose(plan, 'Yes, create a separate draft', 'Haan, alag draft banao')}, {'slug': 'no', 'name': choose(plan, 'No, skip this one', 'Nahi, skip karo')}],
                     suggested='yes')
    # a single item of a grouped field
    if field == 'txn_date':
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'What is the transaction date for {label}?', f'{label} ki date kya hai?'), input_kind='date')
    if field == 'account_slug':
        pool = options['accounts']
        if r['info'].get('account_choices'):
            pool = [a for a in pool if a['slug'] in r['info']['account_choices']]
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'Which account applies to {label}?', f'{label} kis account se/mein hai?'), choices=_choices(pool))
    if field == 'payment_method_slug':
        methods = [m for m in options['payment_methods'] if m['account_slug'] == r['account']]
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'Which payment method applies to {label}?', f'{label} ka payment method kaunsa hai?'), choices=_choices(methods))
    if field == 'priority_slug':
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'What priority applies to {label}?', f'{label} ki priority kya hai?'), choices=_choices(options['priorities']))
    if field == 'transaction_type_slug':
        direction = r['info'].get('direction')
        pool = [t for t in options['types'] if direction in ('inflow', 'outflow', 'transfer') and t['nature'] == direction] \
            or options['types']
        note = {'outflow': ' (paisa jaane wali entry)', 'inflow': ' (paisa aane wali entry)'}.get(direction, '')
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, f'What type of transaction is {label}?', f'{label} ka transaction type kaunsa hai?{note}'), choices=_choices(pool))
    if field == 'category_slug':
        type_slug = r['fields']['transaction_type_slug']
        pool = [c for c in options['categories'] if type_slug in c['type_slugs']]
        payment_to_person = bool(re.search(r'(?i)\b(send|sent|paid|gave|bheja|bheje|diya|diay)\b',
                                           next(e['text'] for e in plan['entries'] if e['id'] == r['entry_id'])))
        english = (f'What was this payment for? Choose a category for {label}.' if payment_to_person
                   else f'Which category applies to {label}?')
        roman = (f'{label} ki payment kis liye thi?' if payment_to_person
                 else f'{label} ki category kaunsi hai?')
        return _make(plan, field, [r], scope='item', group_key=f"i{r['item_id']}",
                     question=choose(plan, english, roman), choices=_choices(pool))
    raise AssertionError(field)


def _group_key(field: str, r: dict) -> str:
    if field == 'payment_method_slug':
        return r['account'] or 'none'
    if field == 'transaction_type_slug':
        return r['info'].get('direction') or 'unknown'
    if field == 'category_slug':
        return r['fields']['transaction_type_slug']
    return 'all'


def compute_questions(plan: dict, answers: dict, options: dict, *, today: date):
    answers = normalize_answers(answers)
    resolved = resolve_all(plan, options, answers, today=today)
    live = [r for r in resolved if not r['excluded']]
    asked, each = answers['asked'], answers['each']
    questions: list[dict] = []
    item_questions = 0

    structured = [r for r in live if r['info'].get('direction_src') == 'structure']
    if structured and answers['convention'] is None:
        questions.append({
            'id': 'convention|batch', 'field': 'convention', 'scope': 'batch', 'required': True, 'input': 'select',
            'question': choose(plan, "Should '+' entries be treated as expenses, and entries outside the chain as money received?", "Kya '+' se jurri entries kharcha hain aur baqi incoming?"),
            'hint': choose(plan, 'Only needed when the transaction wording does not establish direction.', 'Sirf tab jab wording se direction clear na ho.'),
            'choices': [
                {'slug': 'plus_outflow', 'name': choose(plan, "Yes: '+' = expense, separate = incoming", "Haan: '+' = kharcha, baqi = incoming")},
                {'slug': 'plus_inflow', 'name': choose(plan, "Reverse: '+' = incoming, separate = expense", "Ulta: '+' = incoming, baqi = kharcha")},
                {'slug': 'ignore', 'name': choose(plan, 'Ignore the pattern; I will choose each type', 'Pattern ignore karein, main type choose karunga')}],
            'suggested': 'plus_outflow', 'item_ids': [r['item_id'] for r in structured],
            'items': [dict(_brief(plan, r), direction=r['info'].get('direction')) for r in structured[:PREVIEW]],
            'affected_count': len(structured), 'allow_each': False, 'allow_skip': False,
        })

    for field in GROUPED_ORDER + ('txn_time',):
        pending = [r for r in live if field in r['missing'] and r['item_id'] not in asked.get(field, [])]
        if not pending:
            continue
        individually = [r for r in pending if r['item_id'] in each.get(field, [])]
        grouped = [r for r in pending if r['item_id'] not in each.get(field, [])]
        buckets: dict[str, list[dict]] = {}
        for r in grouped:
            buckets.setdefault(_group_key(field, r), []).append(r)
        for key, rows in buckets.items():
            if field == 'txn_time':
                questions.append(_make(plan, field, rows, scope='group', group_key=key, input_kind='time',
                                       question=choose(plan, f'Time is missing for {len(rows)} entries. Set a common time, or keep 12:00 for review.', f'{len(rows)} entries ka waqt nahi mila. Common waqt batayein (warna 12:00 rahega).')))
            elif len(rows) >= 2:
                questions.append(_group_question(plan, options, field, key, rows))
            else:
                individually.extend(rows)
        for r in individually:
            if item_questions >= MAX_ITEM_QUESTIONS:
                break
            questions.append(_item_question(plan, options, field, r))
            item_questions += 1

    for field in ITEM_ONLY_ORDER:
        for r in live:
            if field in r['missing'] and r['item_id'] not in asked.get(field, []) and item_questions < MAX_ITEM_QUESTIONS:
                questions.append(_item_question(plan, options, field, r))
                item_questions += 1
    return questions, resolved


# ---- views and drafts ---------------------------------------------------------------

def _common_value(resolved: list[dict], key: str):
    values = {r['fields'].get(key) for r in resolved if r['fields'].get(key)}
    return next(iter(values)) if len(values) == 1 and all(r['fields'].get(key) for r in resolved) else None


def plan_view(plan: dict, answers: dict, options: dict, *, today: date, session_slug: str | None = None,
              status: str | None = None) -> dict:
    questions, resolved = compute_questions(plan, answers, options, today=today)
    # The optional LLM may reword text, never alter control types, choices,
    # fields, required status, affected items, or input sanitization.
    rewrites = plan.get('question_rewrites') or {}
    for q in questions:
        wording = rewrites.get(q['id'])
        if wording:
            q['question'] = wording['question']
            if wording.get('hint'):
                q['hint'] = wording['hint']
    live = [r for r in resolved if not r['excluded']]
    summary = {}
    for field in ('txn_date', 'account_slug', 'payment_method_slug', 'priority_slug',
                  'transaction_type_slug', 'category_slug'):
        have = [r for r in live if field in r['sources']]
        sources: dict[str, int] = {}
        for r in have:
            sources[r['sources'][field]] = sources.get(r['sources'][field], 0) + 1
        summary[field] = {'total': len(live), 'ready': len(have), 'by_source': sources}
    ctx = plan['context']
    detected = {
        'global_date': _common_value(live, 'txn_date'),
        'global_date_without_year': (live[0]['info'].get('partial_date')
                                     if live and all(r['info'].get('partial_date') == live[0]['info'].get('partial_date')
                                                     and 'txn_date' in r['missing'] for r in live) else None),
        'global_account_slug': ctx.get('account_slug'),
        'global_payment_method_slug': ctx.get('payment_method_slug'),
        'global_priority_slug': ctx.get('priority_slug'),
        'global_type_slug': ctx.get('transaction_type_slug'),
        'global_category_slug': ctx.get('category_slug'),
        'global_time': ctx.get('txn_time'),
    }
    return {
        'session_slug': session_slug, 'status': status,
        'entry_count': len(plan['entries']), 'item_count': len(live),
        'degraded': plan.get('degraded', False), 'degraded_reasons': plan.get('degraded_reasons', []),
        'detected': detected, 'field_summary': summary,
        'items': [{'id': r['item_id'], 'entry_id': r['entry_id'],
                   'text': _clip(next(e['raw'] for e in plan['entries'] if e['id'] == r['entry_id'])),
                   'description': _clip(r['fields']['description'], 60), 'amount': r['fields']['amount'],
                   'direction': r['info'].get('direction'), 'direction_src': r['info'].get('direction_src'),
                   'type_slug': r['fields']['transaction_type_slug'] or None,
                   'missing_fields': list(dict.fromkeys(r['missing'] + r['blocked'])),
                   'field_sources': r['sources']}
                  for r in live],
        'questions': questions,
        'ready': not any(q['required'] for q in questions),
        'note': 'Common details are reviewed here. Every draft still needs your approval before it changes any balance.',
    }


def build_draft_rows(plan: dict, answers: dict, options: dict, *, today: date) -> list[dict]:
    resolved = resolve_all(plan, options, answers, today=today)
    entries = {e['id']: e for e in plan['entries']}
    rows = []
    for r in resolved:
        if r['excluded']:
            continue
        entry = entries[r['entry_id']]
        pending = {}
        if r['info'].get('pending_account'):
            pending['account_slug'] = r['info']['pending_account']
            if r['fields']['payment_method_slug']:
                pending['payment_method_slug'] = r['fields']['payment_method_slug']
        rows.append({
            'raw_input': entry['raw'], 'fields': r['fields'],
            'ambiguous': {'warnings': draft_notes(r), 'source_line': entry['source_line'],
                          'source_part': entry['source_part'], 'field_sources': r['sources'],
                          'direction': r['info'].get('direction'), 'pending': pending},
        })
    return rows
