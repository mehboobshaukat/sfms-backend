"""Step 2: decide every field of every draft item from ALL the evidence.

Precedence, strongest first, for each field:

    user's answer for this item   (the user said so, in this session)
    the entry's own text          ("jazz cash me send kiay" names an account)
    user's common answer / the batch context (instruction lines)
    the AI's suggestion           (only if it names a real option)
    a safe default                (only one account / one default priority)
    missing                       -> the assistant asks, or the draft shows it

Every decision records where it came from ('user', 'text', 'context',
'heading', 'ai', 'derived', 'default'), so the UI can show what was assumed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re

from app.ai import shorthand as sh
from app.ai.matching import find_mentions, match_option
from app.schemas.ai_bulk import Candidate

LOAN_CONTACT_TYPES = ('loan received', 'loan given', 'loan repaid', 'loan collected')
LOAN_SETTLEMENT_TYPES = ('loan repaid', 'loan collected')
CREDIT_SETTLEMENT_TYPES = ('item payment made', 'item payment received')
ASSUMED_SOURCES = {'ai', 'default', 'derived'}
_TOTAL_WORDS = re.compile(r'\b(total|grand total|kul|majmooi|jama total)\b', re.IGNORECASE)
_CURRENCY_CODES = ('PKR', 'USD', 'EUR', 'GBP', 'INR', 'AED', 'SAR', 'CAD', 'AUD', 'JPY', 'KWD')


def empty_answers() -> dict:
    return {'common': {}, 'items': {}, 'skipped': {}, 'each': {}, 'asked': {}, 'convention': None}


def normalize_answers(raw: dict | None) -> dict:
    base = empty_answers()
    if raw:
        base.update({k: raw[k] for k in base if k in raw})
    return base


def number_tokens(text: str) -> set[Decimal]:
    """Numeric evidence across scripts, including amounts touching CJK characters.

    Do not require word boundaries around digits: Unicode letters (e.g. 工资45000巴)
    can be adjacent to an amount. Keep a digit boundary to avoid partial matches.
    Financial shorthand is normalized at the same time.
    """
    import unicodedata

    normalized = unicodedata.normalize('NFKC', text or '')
    normalized = ''.join(str(unicodedata.decimal(char)) if char.isdecimal() else char
                         for char in normalized)
    found: set[Decimal] = set()
    rx = re.compile(r'(?<![0-9.,])(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)'
                    r'(?P<suffix>[kKmM]?)(?![0-9]|[.,][0-9])')
    for match in rx.finditer(normalized):
        try:
            value = Decimal(match['value'].replace(',', ''))
            if match['suffix']:
                value *= Decimal('1000' if match['suffix'].lower() == 'k' else '1000000')
            found.add(value)
        except InvalidOperation:
            continue
    return found


def _norm_direction(value: str | None) -> str | None:
    v = (value or '').strip().lower()
    return v if v in ('outflow', 'inflow', 'transfer') else None


def struct_direction(hint: str | None, convention: str | None) -> str | None:
    """Direction implied by the user's '+' pattern, after their confirmation."""
    if not hint or convention == 'ignore':
        return None
    if convention == 'plus_inflow':
        return {'outflow': 'inflow', 'inflow': 'outflow'}[hint]
    return hint


def _default_type(types: list[dict], nature: str) -> dict | None:
    same = [t for t in types if t['nature'] == nature]
    named = [t for t in same if t['name'].strip().casefold() == ('expense' if nature == 'outflow' else 'income')]
    if len(named) == 1:
        return named[0]
    return same[0] if len(same) == 1 else None


def resolve_item(plan: dict, item: dict, entry: dict, options: dict, answers: dict, *, today: date,
                 is_main: bool = True) -> dict:
    cand = Candidate.model_validate(item['cand'])
    ctx = plan['context']
    own = answers['items'].get(str(item['id']), {})
    common = answers['common']
    text = entry['text']
    types, cats, accts = options['types'], options['categories'], options['accounts']
    methods, priorities, contacts = options['payment_methods'], options['priorities'], options['contacts']
    type_by = {t['slug']: t for t in types}
    cat_by = {c['slug']: c for c in cats}
    acct_by = {a['slug']: a for a in accts}
    pri_by = {p['slug']: p for p in priorities}
    contact_by = {c['slug']: c for c in contacts}
    warnings = [w for w in cand.warnings if w]
    src: dict[str, str] = {}
    missing: list[str] = []
    blocked: list[str] = []
    info: dict = {}

    # ---- direction + type ------------------------------------------------
    word_dir = _norm_direction(cand.direction)
    # The '+' pattern describes the entry's MAIN amount, never extra amounts narrated inside it.
    hint_dir = None if (cand.is_inner or not is_main) else struct_direction(entry.get('hint'), answers.get('convention'))
    if word_dir and hint_dir and word_dir != hint_dir:
        warnings.append("Aap ke '+' pattern aur entry ke alfaaz ki direction mein farq hai; alfaaz ko tarjeeh di gayi. Verify karein.")
    mentioned_types = find_mentions(text, types)
    type_slug = type_src = None
    for source, slug in (('user', own.get('transaction_type_slug')),
                         ('text', mentioned_types[0]['slug'] if len(mentioned_types) == 1 else None),
                         ('user', common.get('transaction_type_slug')),
                         ('context', ctx.get('transaction_type_slug'))):
        if slug and slug in type_by:
            type_slug, type_src = slug, source
            break
    direction = dir_src = None
    if type_slug:
        direction, dir_src = type_by[type_slug]['nature'], 'type'
    else:
        if word_dir:
            direction, dir_src = word_dir, 'words'
        elif hint_dir:
            direction, dir_src = hint_dir, 'structure'
        suggested = match_option(types, cand.transaction_type_name)
        if suggested:
            if direction is None or suggested['nature'] == direction:
                # The AI's own type agrees with the direction: the '+' pattern did not
                # have to decide anything, so the user is not asked to confirm it.
                type_slug, type_src = suggested['slug'], 'ai'
                direction, dir_src = suggested['nature'], 'type'
            else:
                warnings.append(f"AI ne type '{suggested['name']}' suggest kiya tha lekin entry ki direction "
                                f"{'inflow' if direction == 'inflow' else 'outflow' if direction == 'outflow' else direction} lagti hai; type aap ko chunna hoga.")
        if not type_slug and direction == 'outflow':
            derived = _default_type(types, 'outflow')
            if derived:
                type_slug, type_src = derived['slug'], 'derived'
    if type_slug:
        src['transaction_type_slug'] = type_src
    else:
        missing.append('transaction_type_slug')
    nature = type_by[type_slug]['nature'] if type_slug else None
    type_name = type_by[type_slug]['name'].strip().casefold() if type_slug else ''
    info['direction'], info['direction_src'] = direction, dir_src

    # ---- account -----------------------------------------------------------
    acc_mentions = find_mentions(text, accts)
    mth_mentions = find_mentions(text, methods)
    account = acc_src = None
    multi_accounts = None
    if own.get('account_slug') in acct_by:
        account, acc_src = own['account_slug'], 'user'
    elif len(acc_mentions) == 1:
        account, acc_src = acc_mentions[0]['slug'], 'text'
    elif len(acc_mentions) > 1:
        multi_accounts = [a['slug'] for a in acc_mentions]
        warnings.append('Is entry mein ek se zyada accounts ka zikr hai; sahi account chunein.')
    elif len(mth_mentions) == 1 and mth_mentions[0]['account_slug'] in acct_by:
        account, acc_src = mth_mentions[0]['account_slug'], 'text'
    if account is None and not multi_accounts:
        for source, slug in (('user', common.get('account_slug')), ('context', ctx.get('account_slug'))):
            if slug in acct_by:
                account, acc_src = slug, source
                break
    if account is None and not multi_accounts:
        suggested = match_option(accts, cand.account_name)
        if suggested:
            account, acc_src = suggested['slug'], 'ai'
    if account is None and not multi_accounts and len(accts) == 1:
        account, acc_src = accts[0]['slug'], 'default'
    if account:
        src['account_slug'] = acc_src
    elif nature != 'transfer':
        missing.append('account_slug')
        if multi_accounts:
            info['account_choices'] = multi_accounts

    # ---- payment method (belongs to the account) ---------------------------
    method = method_src = None
    account_methods = [m for m in methods if m['account_slug'] == account] if account else []
    if account:
        method_slugs = {m['slug'] for m in account_methods}
        text_methods = [m['slug'] for m in mth_mentions if m['slug'] in method_slugs]
        for source, slug in (('user', own.get('payment_method_slug')),
                             ('text', text_methods[0] if len(text_methods) == 1 else None),
                             ('user', common.get('payment_method_slug')),
                             ('context', ctx.get('payment_method_slug'))):
            if slug and slug in method_slugs:
                method, method_src = slug, source
                break
        if not method:
            same_name = match_option(account_methods, acct_by[account]['name'])
            if same_name:  # account "Cash" -> payment method "Cash"
                method, method_src = same_name['slug'], 'derived'
        if not method:
            suggested = match_option(account_methods, cand.payment_method_name)
            if suggested:
                method, method_src = suggested['slug'], 'ai'
        if not method and len(account_methods) == 1:
            method, method_src = account_methods[0]['slug'], 'default'
    if method:
        src['payment_method_slug'] = method_src
    elif account and nature != 'transfer':
        missing.append('payment_method_slug')
    elif nature != 'transfer':
        blocked.append('payment_method_slug')

    # ---- priority ------------------------------------------------------------
    priority = priority_src = None
    for source, slug in (('user', own.get('priority_slug')), ('user', common.get('priority_slug')),
                         ('context', ctx.get('priority_slug'))):
        if slug and slug in pri_by:
            priority, priority_src = slug, source
            break
    if not priority:
        suggested = match_option(priorities, cand.priority_name)
        if suggested:
            priority, priority_src = suggested['slug'], 'ai'
    if not priority:
        defaults = [p for p in priorities if p.get('is_default')]
        if len(defaults) == 1:
            priority, priority_src = defaults[0]['slug'], 'default'
    if priority:
        src['priority_slug'] = priority_src
    else:
        missing.append('priority_slug')

    # ---- category (must be linked to the type) -----------------------------
    category = category_src = None
    if type_slug:
        def linked(slug):
            return slug in cat_by and type_slug in cat_by[slug]['type_slugs']
        text_cats = [c['slug'] for c in find_mentions(text, cats) if linked(c['slug'])]
        for source, slug in (('user', own.get('category_slug')),
                             ('text', text_cats[0] if len(text_cats) == 1 else None),
                             ('user', common.get('category_slug')),
                             ('context', ctx.get('category_slug'))):
            if slug and linked(slug):
                category, category_src = slug, source
                break
        if not category:
            suggested_any = match_option(cats, cand.category_name)
            if suggested_any and not linked(suggested_any['slug']):
                warnings.append('Category is not linked to this transaction type.')
            suggested = match_option([c for c in cats if linked(c['slug'])], cand.category_name)
            if suggested:
                category, category_src = suggested['slug'], 'ai'
        if category:
            src['category_slug'] = category_src
        else:
            missing.append('category_slug')
    else:
        blocked.append('category_slug')

    # ---- contact ---------------------------------------------------------------
    contact = contact_src = None
    text_contacts = find_mentions(text, contacts, allow_partial=True)
    for source, slug in (('user', own.get('contact_slug')),
                         ('text', text_contacts[0]['slug'] if len(text_contacts) == 1 else None),
                         ('user', common.get('contact_slug'))):
        if slug and slug in contact_by:
            contact, contact_src = slug, source
            break
    if not contact:
        suggested = match_option(contacts, cand.contact_name)
        if suggested:
            contact, contact_src = suggested['slug'], 'ai'
    if contact and category:
        allowed = cat_by[category].get('contact_slugs')
        if allowed is not None and contact not in allowed:
            warnings.append(f"Contact '{contact_by[contact]['name']}' is not linked to this category's groups; choose another contact or category.")
            contact = None
            info['contact_choices'] = allowed
    if contact:
        src['contact_slug'] = contact_src
    elif type_name in LOAN_CONTACT_TYPES:
        missing.append('contact_slug')

    # ---- settlement references: only known, user-owned active records -------------
    def valid_ref(key, value):
        return next((x for x in options.get(key, []) if x['slug'] == value), None)

    loan_code = own.get('loan_code') or cand.loan_code
    loan_entry = valid_ref('loans', loan_code) if loan_code else None
    if type_name in LOAN_SETTLEMENT_TYPES:
        expected = 'received' if type_name == 'loan repaid' else 'given'
        if loan_entry and loan_entry['loan_type'] == expected and (not contact or loan_entry['contact_slug'] == contact):
            loan_code = loan_entry['slug']
            src['loan_code'] = 'user' if own.get('loan_code') else 'ai'
        else:
            if loan_code:
                warnings.append('Loan reference is unknown, belongs to a different contact or has the wrong loan direction.')
            loan_code = None
            missing.append('loan_code')
    else:
        loan_code = None
    credit_slug = own.get('credit_purchase_slug') or cand.credit_purchase_slug
    credit_entry = valid_ref('credit_purchases', credit_slug) if credit_slug else None
    if type_name in CREDIT_SETTLEMENT_TYPES:
        expected = 'received' if type_name == 'item payment made' else 'given'
        if credit_entry and credit_entry['credit_type'] == expected:
            credit_slug = credit_entry['slug']
            src['credit_purchase_slug'] = 'user' if own.get('credit_purchase_slug') else 'ai'
        else:
            if credit_slug:
                warnings.append('Credit purchase reference is unknown or has the wrong payment direction.')
            credit_slug = None
            missing.append('credit_purchase_slug')
    else:
        credit_slug = None

    # ---- amount ------------------------------------------------------------------
    amount = ''
    amount_src = None
    ambiguous_quantity = bool(sh.QUANTITY_PRICE.search(text) and not _TOTAL_WORDS.search(text))
    if own.get('amount'):
        try:
            value = Decimal(str(own['amount']).replace(',', ''))
            if value.is_finite() and value > 0:
                amount, amount_src = str(value), 'user'
        except InvalidOperation:
            pass
    elif ambiguous_quantity:
        warnings.append('Quantity and price may mean per-item or total; confirm the final amount.')
    elif cand.amount is not None:
        try:
            value = Decimal(cand.amount.replace(',', ''))
            numeric_source = number_tokens(text)
            if not value.is_finite() or value <= 0:
                warnings.append('Amount must be positive.')
            elif numeric_source and value not in numeric_source:
                warnings.append('AI amount was not stated exactly in the note; supply/confirm the amount.')
            else:
                amount, amount_src = str(value), 'text'
        except (InvalidOperation, ValueError):
            warnings.append('Unrecognized amount. Please supply the exact amount.')
    if cand.amount_note:
        warnings.append(cand.amount_note)
    explicit_codes = {code for code in _CURRENCY_CODES if re.search(rf'\b{code}\b', text, re.IGNORECASE)}
    if explicit_codes and explicit_codes != {options.get('currency_code', 'PKR')} and amount_src != 'user':
        amount = ''
        warnings.append('Currency differs from your account currency. No conversion was made. Verify before posting.')
    if amount:
        src['amount'] = amount_src or 'text'
    else:
        missing.append('amount')

    # ---- date ------------------------------------------------------------------------
    # The AI has already normalized the entry's date to ISO YYYY-MM-DD.
    # Verify syntax and calendar validity here; do NOT gate it on whether
    # English or multilingual regexes understand the original source script.
    ai_date = None
    if cand.txn_date:
        try:
            raw_date = cand.txn_date.strip()
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date, re.ASCII):
                raise ValueError('Date must be an ISO calendar date')
            ai_date = date.fromisoformat(raw_date).isoformat()
        except (AttributeError, TypeError, ValueError):
            warnings.append('AI date was not a valid YYYY-MM-DD date; please provide/confirm it.')

    # Independently parseable source dates can detect a genuine disagreement.
    # Parsing is an OPTIONAL cross-check, never a prerequisite for accepting
    # a valid ISO date (e.g. Korean, Spanish, Indonesian inputs).
    explicit_source_date = sh.parse_header_date(text)
    source_iso = explicit_source_date.isoformat() if explicit_source_date else None
    header_iso = entry.get('header_date')
    conflict = False
    if ai_date and source_iso and ai_date != source_iso:
        warnings.append('AI date conflicts with the explicit date in this entry; please confirm the transaction date.')
        conflict = True
    elif ai_date and header_iso and ai_date != header_iso and source_iso != ai_date:
        # Without an independently verified per-entry override, choosing the
        # model's value OR the header risks silently assigning a wrong date.
        warnings.append('AI date conflicts with the batch date; please confirm which date applies.')
        conflict = True

    chosen = date_src = None
    own_date = own.get('txn_date')
    if own_date:
        # The user's explicit answer takes precedence over AI suggestions.
        chosen, date_src = own_date, 'user'
    elif conflict:
        # Keep the draft editable; a conflicting date requires user review.
        pass
    elif ai_date:
        chosen, date_src = ai_date, 'ai'
    elif header_iso:
        chosen, date_src = header_iso, 'heading'
    elif common.get('txn_date'):
        chosen, date_src = common['txn_date'], 'user'
    if chosen:
        src['txn_date'] = date_src
    else:
        missing.append('txn_date')
        if entry.get('header_partial'):
            info['partial_date'] = entry['header_partial']

    # ---- time ---------------------------------------------------------------------------
    time_value = time_src = None
    for source, value in (('user', own.get('txn_time')), ('text', sh.parse_time(text)),
                          ('ai', cand.txn_time if cand.txn_time and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', cand.txn_time) and re.search(r'\d{1,2}[:.]\d{2}', text) else None),
                          ('user', common.get('txn_time')), ('context', ctx.get('txn_time'))):
        if value:
            time_value, time_src = value, source
            break
    if not time_value:
        time_value, time_src = '12:00', 'default'
        missing.append('txn_time')
    src['txn_time'] = time_src

    # ---- inner amounts with no clear direction: ask whether to keep ------------------
    excluded = own.get('include') == 'no'
    if cand.is_inner and direction is None and 'include' not in own:
        missing.append('include')
    if cand.is_inner:
        warnings.append('Yeh amount kisi doosri entry ke andar likha tha; confirm karein ke yeh alag transaction hai.')

    # Transfer accounts must be independently named. For ordinary income/expense,
    # the existing single-account resolver remains authoritative.
    from_slug = account if account and nature == 'outflow' else None
    to_slug = account if account and nature == 'inflow' else None
    if nature == 'transfer':
        explicit_from = match_option(accts, cand.from_account_name)
        explicit_to = match_option(accts, cand.to_account_name)
        from_slug = own.get('from_account_slug') or (explicit_from['slug'] if explicit_from else None)
        to_slug = own.get('to_account_slug') or (explicit_to['slug'] if explicit_to else None)
        if from_slug == to_slug and from_slug:
            warnings.append('Transfer requires distinct source and destination accounts.')
            to_slug = None
        for field, value in (('from_account_slug', from_slug), ('to_account_slug', to_slug)):
            if not value or value not in acct_by:
                missing.append(field)
            else:
                src[field] = 'user' if own.get(field) else 'ai'
    # Non-transfer explicit account fields are checked against the transaction nature.
    elif nature in ('inflow', 'outflow') and account is None:
        explicit = match_option(accts, cand.to_account_name if nature == 'inflow' else cand.from_account_name)
        if explicit:
            if nature == 'inflow':
                to_slug = explicit['slug']
            else:
                from_slug = explicit['slug']
            if 'account_slug' in missing:
                missing.remove('account_slug')
            src['account_slug'] = 'ai'
    if nature == 'transfer':
        # Payment method is always tied to the source account for a transfer.
        owner_methods = [m for m in methods if m['account_slug'] == from_slug]
        if not method or method not in {m['slug'] for m in owner_methods}:
            suggested = match_option(owner_methods, cand.payment_method_name)
            if suggested:
                method = suggested['slug']
                src['payment_method_slug'] = 'ai'
            elif len(owner_methods) == 1:
                method = owner_methods[0]['slug']
                src['payment_method_slug'] = 'default'
            else:
                method = None
                missing.append('payment_method_slug')
        if from_slug and not to_slug:
            warnings.append('Transfer destination needs confirmation.')
    def optional_amount(raw, label):
        if raw is None or str(raw).strip() == '':
            return None
        try:
            value = Decimal(str(raw).replace(',', ''))
            if value.is_finite() and value >= 0:
                return str(value)
        except InvalidOperation:
            pass
        warnings.append(f'Invalid {label}; review in draft.')
        return None
    charges = optional_amount(cand.charges, 'charges')
    if charges is not None and Decimal(charges) > 0 and Decimal(charges) not in number_tokens(text):
        charges = None
        warnings.append('AI suggested fees not evidenced by the note; review charges in draft.')
    due_date = None
    if type_name in ('loan received', 'loan given') and cand.due_date:
        try:
            due_date = date.fromisoformat(cand.due_date).isoformat()
        except ValueError:
            warnings.append('Loan due date was unclear; review in draft.')
    charges_borne_by = cand.charges_borne_by if cand.charges_borne_by in ('from_account', 'to_account') else 'from_account'
    reference_no = (cand.reference_no or '').strip()[:100]
    if reference_no and reference_no.casefold() not in text.casefold():
        warnings.append('Reference number was not clearly present; left blank for review.')
        reference_no = ''
    remarks = (cand.remarks or '').strip()[:1000] or None
    fields = {
        'txn_date': chosen or '',
        'txn_time': f'{time_value}:00',
        'transaction_type_slug': type_slug or '',
        'category_slug': category or '',
        'description': (cand.description.strip() or text)[:500],
        'from_account_slug': from_slug,
        'to_account_slug': to_slug,
        'payment_method_slug': method or '',
        'contact_slug': contact,
        'priority_slug': priority or '',
        'amount': amount,
        'charges': charges if charges is not None else '0.00',
        'charges_borne_by': charges_borne_by,
        'reference_no': reference_no or None,
        'remarks': remarks,
        'due_date': due_date,
        'loan_code': loan_code, 'credit_purchase_slug': credit_slug,
    }
    if account and nature is None:
        # Type unknown yet: remember the account so it is placed once the type is chosen.
        info['pending_account'] = account
    return {'item_id': item['id'], 'entry_id': entry['id'], 'excluded': excluded, 'fields': fields,
            'sources': src, 'warnings': list(dict.fromkeys(warnings)), 'missing': missing,
            'blocked': blocked, 'nature': nature, 'type_name': type_name, 'account': account, 'info': info}


def resolve_all(plan: dict, options: dict, answers: dict, *, today: date) -> list[dict]:
    answers = normalize_answers(answers)
    entries = {e['id']: e for e in plan['entries']}
    seen_entries: set[int] = set()
    resolved = []
    for item in plan['items']:
        is_main = item['entry_id'] not in seen_entries
        seen_entries.add(item['entry_id'])
        resolved.append(resolve_item(plan, item, entries[item['entry_id']], options, answers, today=today,
                                     is_main=is_main))
    # Numbers present in an entry that no extracted item represents: never lose them silently.
    for entry in plan['entries']:
        if sh.QUANTITY_PRICE.search(entry['text']):
            continue
        mine = [i for i in plan['items'] if i['entry_id'] == entry['id']]
        extracted: set[Decimal] = set()
        for it in mine:
            if it['cand'].get('amount') is not None:
                try:
                    extracted.add(Decimal(str(it['cand']['amount']).replace(',', '')))
                except InvalidOperation:
                    pass
        unmatched = sorted(number_tokens(entry['text']) - extracted)
        if unmatched:
            names = ', '.join(str(n) for n in unmatched)
            note = (f'This note mentions other numeric values ({names}) not represented by an AI draft. '
                    'Check whether an additional financial transaction is missing.')
            for r in resolved:
                if r['entry_id'] == entry['id']:
                    r['warnings'].append(note)
    return resolved


def draft_notes(r: dict) -> list[str]:
    """Human-readable notes for the review draft (only what still needs the user)."""
    notes = list(r['warnings'])
    labels = {
        'amount': 'Exact amount is required.',
        'transaction_type_slug': 'Choose an existing transaction type.',
        'category_slug': 'Choose a linked category.',
        'account_slug': 'Choose the account that receives or pays this money.',
        'payment_method_slug': 'Select payment method for the relevant account.',
        'priority_slug': 'Choose priority.',
        'txn_date': 'Date is unconfirmed; enter and verify it before confirmation.',
        'contact_slug': 'Loan transaction requires a contact linked to the category.',
        'loan_code': 'Select the correct existing loan to settle.',
        'credit_purchase_slug': 'Select the correct existing credit purchase to settle.',
        'from_account_slug': 'Select the transfer source account.',
        'to_account_slug': 'Select the transfer destination account.',
    }
    sources = r['sources']
    if sources.get('txn_date') == 'heading':
        notes.append('Date inherited from the batch heading; please verify.')
    if sources.get('txn_time') == 'default':
        notes.append('Time not supplied; 12:00 is a placeholder. Please review.')
    for field in r['missing'] + r['blocked']:
        if field in labels:
            notes.append(labels[field])
    if r['nature'] == 'transfer':
        notes.append('Transfer requires two distinct accounts. Please review.')
    assumed = [name for key, name in (('account_slug', 'Account'), ('payment_method_slug', 'Payment method'),
                                      ('transaction_type_slug', 'Type'), ('category_slug', 'Category'),
                                      ('priority_slug', 'Priority'), ('contact_slug', 'Contact'))
               if sources.get(key) in ASSUMED_SOURCES]
    if assumed:
        notes.append(f"Auto-selected, please verify: {', '.join(assumed)}.")
    return list(dict.fromkeys(notes))
