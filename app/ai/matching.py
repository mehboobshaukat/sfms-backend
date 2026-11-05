"""Name matching helpers for the AI draft pipeline. Pure functions, no I/O.

Why this exists: an LLM (or the user) rarely writes an option name exactly as
it is stored. "Jazz Cash", "jazzcash" and "JazzCash" are one account; "Bhai
Asad" should find the contact "Asad". An exact casefold comparison silently
dropped those fields and left blank dropdowns in the review drafts.

Matching is deliberately conservative: an ambiguous match returns None so the
caller asks the user instead of guessing.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
# Words that carry no identity in Roman-Urdu / English notes about people.
_HONORIFICS = {"bhai", "bhaiya", "baji", "api", "aapi", "bhabhi", "uncle", "aunty", "chacha", "mama",
               "khala", "phuphi", "dada", "dadi", "nana", "nani", "mr", "mrs", "ms", "sir", "madam",
               "baba", "ustad", "sahab", "sahib", "ji"}


def norm(value: str | None) -> str:
    """Casefolded text with punctuation collapsed: 'Jazz-Cash!' -> 'jazz cash'."""
    if not value:
        return ""
    return _NON_WORD.sub(" ", str(value).casefold()).strip()


def squash(value: str | None) -> str:
    """norm() without spaces, so 'jazz cash' == 'jazzcash'."""
    return norm(value).replace(" ", "")


def tokens(value: str | None) -> list[str]:
    return [t for t in norm(_CAMEL.sub(" ", str(value or ""))).split() if t]


def _similar(a: str, b: str) -> float:
    try:  # rapidfuzz is optional; difflib is good enough for names
        from rapidfuzz import fuzz  # type: ignore
        return fuzz.ratio(a, b) / 100.0
    except Exception:
        return SequenceMatcher(None, a, b).ratio()


def match_option(items: list[dict], name: str | None, *, min_ratio: float = 0.86) -> dict | None:
    """Return the single option whose name best matches `name`, else None.

    Order: exact casefold -> squashed equality -> same token set -> unique fuzzy
    match with a clear margin over the runner-up.
    """
    if not name or not str(name).strip():
        return None
    wanted = str(name).strip()
    for step in (lambda x: x.strip().casefold(), squash, lambda x: " ".join(sorted(tokens(x)))):
        hits = [i for i in items if i.get("name") and step(i["name"]) == step(wanted) and step(wanted)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    target = squash(wanted)
    if len(target) < 4:
        return None
    scored = sorted(((_similar(squash(i["name"]), target), i) for i in items if i.get("name")),
                    key=lambda v: -v[0])
    if scored and scored[0][0] >= min_ratio and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return scored[0][1]
    return None


def _name_regex(name: str) -> re.Pattern | None:
    toks = tokens(name)
    if not toks:
        return None
    body = r"[\s\-_.]*".join(re.escape(t) for t in toks)
    if len(toks[-1]) >= 4:
        body += r"(?:e?s)?"  # 'expense' also finds 'expenses'
    return re.compile(r"(?<![^\W_])" + body + r"(?![^\W_])", re.IGNORECASE | re.UNICODE)


def find_mentions(text: str, items: list[dict], *, allow_partial: bool = False) -> list[dict]:
    """Options whose names appear in `text`, ignoring spacing/camel-case.

    Overlapping hits keep the longest one ('jazz cash' names JazzCash, not also
    Cash). With allow_partial a distinctive single token of a multi-word name
    (e.g. 'Asad' for 'Bhai Asad') also counts, but only if it points at exactly
    one option.
    """
    spans: list[tuple[int, int, dict]] = []
    for item in items:
        rx = _name_regex(item.get("name") or "")
        if rx:
            spans.extend((m.start(), m.end(), item) for m in rx.finditer(text))
    if allow_partial:
        token_owner: dict[str, list[dict]] = {}
        for item in items:
            for t in tokens(item.get("name")):
                if len(t) >= 3 and t not in _HONORIFICS:
                    token_owner.setdefault(t, []).append(item)
        for t, owners in token_owner.items():
            if len({o["slug"] for o in owners}) != 1:
                continue
            for m in re.finditer(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])", text, re.IGNORECASE | re.UNICODE):
                spans.append((m.start(), m.end(), owners[0]))
    spans.sort(key=lambda v: -(v[1] - v[0]))
    kept: list[tuple[int, int, dict]] = []
    for start, end, item in spans:
        if any(start < e and s < end for s, e, _ in kept):
            continue
        kept.append((start, end, item))
    kept.sort(key=lambda v: v[0])
    seen, result = set(), []
    for _, _, item in kept:
        if item["slug"] not in seen:
            seen.add(item["slug"])
            result.append(item)
    return result
