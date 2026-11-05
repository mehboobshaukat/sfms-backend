"""Security boundary for untrusted AI-import notes.

This AI feature is a transaction *extractor*, never a SQL / administration
interface. Reject clear attempts to issue database commands, to exfiltrate
records/secrets, or to override the model's instructions BEFORE calling any
provider or writing an AI session. Do not log or return the submitted text.

Detection is defense-in-depth, not a SQL-injection sanitizer. The actual
protection remains: no model DB tools, ORM parameter binding, authenticated
user-scoped lookups, and explicit user approval through normal transaction
validation. Do not depend on regex to authorize operations.
"""
from __future__ import annotations

import re

from fastapi import HTTPException


_SQL_COMMAND = re.compile(
    r"(?im)(?:^|[;\n`])\s*(?:\d{1,3}\s*[.)]\s*)?"
    r"(?:"
    r"select\s+.{1,120}?\s+from\s+\w+"
    r"|with\s+\w+\s+as\s*\(\s*select\b"
    r"|(?:delete\s+from|update\s+\w+\s+set|insert\s+into|"
    r"drop\s+(?:table|database|schema)|truncate\s+(?:table\s+)?|"
    r"alter\s+(?:table|database)|create\s+(?:table|database|role|user)|"
    r"grant\s+\w+\s+on|revoke\s+\w+\s+on)\b"
    r")",
)
_SQL_INJECTION = re.compile(
    r"(?i)(?:['\"]\s*\bor\b\s+['\"]?1['\"]?\s*['\"]?=\s*['\"]?1['\"]?\s*(?:--|#|/\*))"
    r"|(?:\bunion\s+(?:all\s+)?select\b)"
    r"|(?:\bxp_cmdshell\b)",
)
_SQL_FENCE = re.compile(r"(?is)```\s*(?:sql|postgresql|psql)\b")
_SHELL_COMMAND = re.compile(
    r"(?im)(?:^|[;\n`])\s*(?:sudo\s+)?(?:"
    r"(?:cat|type)\s+(?:\.env|/etc/passwd)\b"
    r"|(?:rm\s+-rf|curl\s+[-]|wget\s+https?://|"
    r"powershell(?:\.exe)?\s+-enc|python3?\s+-c|"
    r"bash\s+-c|cmd(?:\.exe)?\s+/c)\b"
    r"|os\.system\s*\(|subprocess\.(?:run|call|Popen)\s*\("
    r")"
)
_DB_QUERY_INSTRUCTION = re.compile(
    r"(?i)\b(?:run|execute|perform|issue|process)\s+"
    r"(?:this\s+|the\s+|a\s+|my\s+)?(?:sql\s+|database\s+)?(?:query|command|statement|script)\b"
    r"|\b(?:query|access)\s+(?:the\s+|my\s+)?(?:database|db|tables?)\b"
    r"|\b(?:delete|erase|wipe|truncate|drop)\s+(?:all\s+)?(?:my\s+)?"
    r"(?:database|tables?|users?|transactions?|accounts?|records?)\b"
    r"|\b(?:database|db)\s+(?:ki|ka|se)\s+(?:sari|saari|sab|tamam)\s+"
    r"(?:tables?|data|records?)\s+(?:dikhao|bhejo|nikalo|delete|hatao)\b"
    r"|\b(?:users?|accounts?|transactions?)\s+(?:ka|ki)\s+"
    r"(?:sara|saara|sari|saari|sab|tamam)\s+(?:data|records?)\s+(?:dikhao|bhejo|nikalo)\b",
)
_EXFILTRATION = re.compile(
    r"(?i)\b(?:show|list|dump|export|extract|reveal|print|leak|steal|give\s+me|send\s+me|fetch)"
    r"\s+(?:the\s+|all\s+|every\s+|other\s+users?'?\s+|my\s+){0,5}"
    r"(?:database|db\s+tables?|all\s+transactions?|users?'?\s+records?|other\s+users?|"
    r"customers?'?\s+data|api\s*keys?|passwords?|secret\s+keys?|access\s+tokens?|"
    r"private\s+keys?|credentials?|environment\s+variables?|users?|transactions?|"
    r"account\s+balances?|personal\s+data|financial\s+data|"
    r"database[_\s-]*url|db[_\s-]*password)\b"
)
_PROMPT_OVERRIDE = re.compile(
    r"(?i)\b(?:ignore|override|bypass|discard|forget|disregard)\s+"
    r"(?:all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+|system\s+|developer\s+){0,6}"
    r"(?:instructions?|rules?|safety\s+checks?|system\s+prompt|developer\s+prompt)\b"
    r"|\b(?:reveal|show|print|dump|repeat)\s+(?:your\s+|the\s+)?"
    r"(?:hidden\s+|system\s+|developer\s+)(?:system\s+|developer\s+)?(?:prompt|instructions?|message)\b"
    r"|\b(?:you\s+are\s+now|act\s+as)\s+(?:an?\s+)?(?:unrestricted|jailbroken|developer\s+mode)\b"
)
# Keep credentials out of cloud-model prompts / saved raw notes. These patterns
# intentionally target *assignments* and common bearer/key formats, not words
# such as "password manager" or "API subscription" in legitimate descriptions.
_EMBEDDED_SECRET = re.compile(
    r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"database[_ -]?url|db[_ -]?password|password|private[_ -]?key)\s*[:=]\s*\S{6,}"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b"
    r"|\bsk-[A-Za-z0-9_-]{18,}\b"
    r"|\bAIza[A-Za-z0-9_-]{25,}\b"
)


_REJECTION = (
    "This assistant only prepares financial transaction drafts. "
    "Database commands, access to private records, credentials, and "
    "instructions to override safeguards are not supported. "
    "Remove those instructions and submit only your transaction notes. "
    "Nothing was posted."
)
_SECRETS_REJECTION = (
    "Your message may contain a password, token, or API key. "
    "Remove sensitive credentials before using the AI Assistant. "
    "Nothing was sent to the AI provider or posted."
)


def validate_transaction_note(raw_text: str) -> None:
    """Reject obvious misuse without echoing or persisting the original text.

    Financial descriptions that merely mention SQL courses, API fees, or
    password-manager subscriptions are allowed. This guard complements (not
    replaces) authentication, parameterized DB queries and posting controls.
    """
    if _EMBEDDED_SECRET.search(raw_text):
        raise HTTPException(status_code=422, detail=_SECRETS_REJECTION)
    if any(rule.search(raw_text) for rule in (
        _SQL_COMMAND, _SQL_INJECTION, _SQL_FENCE, _SHELL_COMMAND,
        _DB_QUERY_INSTRUCTION, _EXFILTRATION, _PROMPT_OVERRIDE,
    )):
        raise HTTPException(status_code=422, detail=_REJECTION)
