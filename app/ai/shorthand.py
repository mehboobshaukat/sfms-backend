"""Deterministic reading of the user's note shorthand. No LLM, no database.

Users write bulk notes in their own shorthand, for example

    350(fare) + 50(tyre check) + 50(milk, baji gave 80 and I bought 20 snacks)
    100(aloo) + 30(snacks) 3500(Bhai gave this for Mushtaq)
    4000(fridge man) + 100(fare) + 250(fuel) (1000 Bhai Asad gave cash)(5000 Bhai Shair sent)

This module splits such lines into entries and records *how* each entry was
joined, because that structure often carries meaning: entries chained with '+'
are normally money going out, while an entry standing apart from the '+' chain
is normally money coming in. The structure is only a HINT. Explicit wording in
the entry always wins, and the user is asked to confirm the convention once per
batch (see planner.py), so a person who writes differently is not misread.
"""
from __future__ import annotations

from datetime import date, datetime
import re

_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
NAMED_DATE = re.compile(rf"(?<!\w)(\d{{1,2}})[\s/\-]+({_MONTHS})[\s,\-/]+(\d{{4}})(?!\w)", re.IGNORECASE)
NUMERIC_DATE = re.compile(r'(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})(?!\d)')
ISO_DATE = re.compile(r'(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)')
MONTH_DAY_ONLY = re.compile(rf"(?<!\w)(\d{{1,2}})[\s/\-]+({_MONTHS})(?!\w)", re.IGNORECASE)
TIME_TOKEN = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[ap]\.?m\.?)?(?!\d)"
                        r"|(?<!\d)(?:1[0-2]|0?[1-9])\s*(?:[ap]\.?m\.?|baje)(?!\w)", re.IGNORECASE)

TODAY_WORD = re.compile(r"(?<!\w)(?:aaj|today|ajj)(?!\w)", re.IGNORECASE)
YESTERDAY_WORD = re.compile(r"(?<!\w)yesterday(?!\w)", re.IGNORECASE)
EXPENSE_VERBS = re.compile(r"(?<!\w)(?:paid|bought|spent|received|earned|income|diye|diay|diya|khareeda|bheja|bheje|send|sent)(?!\w)", re.IGNORECASE)

QUANTITY_PRICE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:registers?|notebooks?|books?|shirts?|pencils?|pens?|"
    r"items?|pieces?|pcs|packets?|bottles?)\s*(?:of|for|at|@|each|per|ki|ke|ka)?\s*"
    r"\d+(?:\.\d+)?\b", re.IGNORECASE
)

# Words that say "this applies to every entry" (Roman Urdu, Urdu, English).
GLOBAL_WORDS = re.compile(
    r"(?<!\w)(?:all|every|everything|entire|whole|sab|sabhi|sabi|saray|saare|sare|sary|sari|saari|"
    r"tamam|har|hr|sb|سب|تمام|ساری|سارے)(?!\w)", re.IGNORECASE)
# Words that name a field the user is giving a value for.
FACET_WORDS = re.compile(
    r"(?<!\w)(?:account|acct|akaunt|khata|khaata|payment|method|pay|priority|ahmiyat|category|type|"
    r"date|dated|tareekh|tarikh|time|waqt|aaj|today|kal|yesterday|parso|transactions?|txns?|entries|hisaab|"
    r"records?)(?!\w)", re.IGNORECASE)

_AMT = r"(?:(?:rs\.?|pkr|inr|usd|aed|sar)\s*)?\d[\d,]*(?:\.\d+)?"
# After a ')', does a NEW entry start (without a '+')?  '3500(' or '(1000 Bhai ...)'
_NEXT_ENTRY = re.compile(rf"[\s,;]*(?=(?:{_AMT})\s*\(|\(\s*{_AMT}(?:\s|\)))", re.IGNORECASE)
_LEADING_PAREN_AMOUNT = re.compile(rf"^\(\s*(?P<amt>{_AMT})\s*(?P<rest>.*)\)$", re.IGNORECASE | re.DOTALL)
_ITEM_PREFIX = re.compile(rf"^(?:{_AMT})\s*\(", re.IGNORECASE)


def parse_header_date(line: str) -> date | None:
    """A full date (day month year, or ISO) found in a non-monetary line."""
    named = NAMED_DATE.search(line)
    try:
        if named:
            month = 'Sep' if named.group(2).lower() == 'sept' else named.group(2)
            return datetime.strptime(f'{named.group(1)} {month[:3].title()} {named.group(3)}', '%d %b %Y').date()
        iso = ISO_DATE.search(line)
        if iso:
            return date.fromisoformat(iso.group(0))
    except ValueError:
        return None
    return None


def parse_partial_date(line: str) -> str | None:
    """'15 September' with no year -> '15-09'. The year is never guessed."""
    if parse_header_date(line) is not None or NUMERIC_DATE.search(line):
        return None
    m = MONTH_DAY_ONLY.search(line)
    if not m:
        return None
    try:
        token = 'Sep' if m.group(2).lower() == 'sept' else m.group(2)
        month = datetime.strptime(token[:3].title(), '%b').month
        day = int(m.group(1))
        date(2000, month, day)  # validates the day, Feb 29 included
        return f'{day:02d}-{month:02d}'
    except ValueError:
        return None


def parse_time(line: str) -> str | None:
    """First clock time in the text as 'HH:MM', or None."""
    m = TIME_TOKEN.search(line)
    if not m:
        return None
    raw = m.group(0).lower().replace('.', '').strip()
    pm = bool(re.search(r'p\s*m', raw))
    am = bool(re.search(r'a\s*m', raw))
    nums = re.findall(r'\d+', raw)
    hour = int(nums[0])
    minute = int(nums[1]) if len(nums) > 1 else 0
    if pm and hour < 12:
        hour += 12
    if am and hour == 12:
        hour = 0
    if not (0 <= hour <= 23):
        return None
    return f'{hour:02d}:{minute:02d}'


def strip_date_time(line: str) -> str:
    """The line with date and time tokens removed (what remains is 'content')."""
    out = NAMED_DATE.sub(' ', line)
    out = NUMERIC_DATE.sub(' ', out)
    out = MONTH_DAY_ONLY.sub(' ', out)
    return TIME_TOKEN.sub(' ', out)


def has_amount(line: str) -> bool:
    """True when digits remain after removing dates and times."""
    return bool(re.search(r'\d', strip_date_time(line)))


def is_instruction_line(line: str, option_names: list[str] | None = None) -> bool:
    """A line that gives batch-wide context rather than a transaction.

    Rule: it carries no amount (digits left after removing dates/times) AND it
    either contains a date/time, a "for all"/field word, or is just an option
    name. Lines with no digits and no such cue stay entries, because a note
    like "kiraya diya do hazar" may state its amount in words.
    """
    stripped = line.strip()
    if not stripped or has_amount(stripped):
        return False
    residue = re.sub(r'[\W_]+', ' ', strip_date_time(stripped)).strip()
    if EXPENSE_VERBS.search(residue):
        return False  # "paid Ali on 15 Sep" describes a payment, not a heading
    has_date = bool(NAMED_DATE.search(stripped) or NUMERIC_DATE.search(stripped)
                    or MONTH_DAY_ONLY.search(stripped) or TIME_TOKEN.search(stripped))
    if has_date and not residue:
        return True
    if GLOBAL_WORDS.search(residue) or FACET_WORDS.search(residue) or TODAY_WORD.search(residue):
        return True
    if has_date:
        return False
    squashed = re.sub(r'[\W_]+', '', stripped).casefold()
    return any(squashed == re.sub(r'[\W_]+', '', n).casefold() for n in (option_names or []) if n)


def _balanced_end(text: str, start: int) -> int | None:
    """Index just after the ')' matching the '(' at text[start], or None."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _normalise_part(raw: str) -> str:
    """'(1000 Bhai Asad gave cash)' -> '1000(Bhai Asad gave cash)'; others unchanged."""
    raw = raw.strip()
    if raw.startswith('(') and _balanced_end(raw, 0) == len(raw):
        m = _LEADING_PAREN_AMOUNT.match(raw)
        if m:
            return f"{m.group('amt').strip()}({m.group('rest').strip()})"
    return raw


def _looks_like_entry(part: str) -> bool:
    return bool(re.search(r'\d', part) and re.search(r'[^\W\d_]', part, re.UNICODE))


def _scan_separators(line: str) -> list[tuple[str, int, int]]:
    """Top-level separators as (kind, start, end): 'plus' or 'space'."""
    seps: list[tuple[str, int, int]] = []
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
            if depth == 0:
                rest = line[i + 1:]
                if not rest.lstrip().startswith('+'):
                    m = _NEXT_ENTRY.match(line, i + 1)
                    if m:  # may be zero-width, e.g. ')(1000 ...)'
                        seps.append(('space', i + 1, m.end()))
                        i = m.end()
                        continue
        elif ch == '+' and depth == 0:
            seps.append(('plus', i, i + 1))
        i += 1
    return seps


def _peel_global_tail(part: str) -> tuple[str, str | None]:
    """'50(tyre) sab cash se' -> ('50(tyre)', 'sab cash se'), only for a "for all" tail."""
    if not _ITEM_PREFIX.match(part) and not part.startswith('('):
        return part, None
    start = part.find('(')
    end = _balanced_end(part, start) if start >= 0 else None
    if not end:
        return part, None
    tail = part[end:].strip()
    if tail and not re.search(r'\d', tail) and GLOBAL_WORDS.search(tail):
        return part[:end].strip(), tail
    return part, None


def split_line(line: str) -> dict:
    """Split one physical line into entries, keeping how they were joined.

    Returns {'parts': [{'text', 'raw', 'chain', 'hint'}], 'inline_instructions': [str]}.
    `hint` is 'outflow' (entry belongs to a '+' chain), 'inflow' (entry stands
    apart from a '+' chain on the same line) or None (no structural evidence).
    """
    line = line.strip()
    seps = _scan_separators(line)
    prev = 0
    pieces: list[tuple[str, str | None]] = []  # (text, separator-before)
    before: str | None = None
    for kind, s, e in seps:
        pieces.append((line[prev:s].strip(), before))
        before, prev = kind, e
    pieces.append((line[prev:].strip(), before))
    pieces = [(t, b) for t, b in pieces if t]
    instructions: list[str] = []

    def single(text: str) -> dict:
        return {'parts': [{'text': _normalise_part(text), 'raw': text, 'chain': False, 'hint': None}],
                'inline_instructions': []}

    if len(pieces) <= 1:
        text, tail = _peel_global_tail(pieces[0][0]) if pieces else (line, None)
        if tail:
            instructions.append(tail)
        out = single(text or line)
        out['inline_instructions'] = instructions
        return out
    peeled = []
    for text, b in pieces:
        text, tail = _peel_global_tail(text)
        if tail:
            instructions.append(tail)
        peeled.append((text, b))
    if not all(_looks_like_entry(t) for t, _ in peeled):
        # Arithmetic such as '100 + 20' or prose: keep the line whole for review.
        out = single(line)
        return out
    n = len(peeled)
    parts = []
    for idx, (text, b) in enumerate(peeled):
        after = peeled[idx + 1][1] if idx + 1 < n else None
        chain = (b == 'plus') or (after == 'plus')
        parts.append({'text': _normalise_part(text), 'raw': text, 'chain': chain, 'hint': None})
    if any(p['chain'] for p in parts):
        for p in parts:
            p['hint'] = 'outflow' if p['chain'] else 'inflow'
    return {'parts': parts, 'inline_instructions': instructions}
