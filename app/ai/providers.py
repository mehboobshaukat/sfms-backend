"""AI parsing providers: Groq free plan, Gemini free tier, or local Ollama.

Every provider returns a validated pydantic model; none has database access.
Do not silently switch to a billable OpenAI endpoint on failure.

Free-tier resilience (all optional, all free):
  * one short retry on a rate limit / 5xx that tells us how long to wait
  * an optional SECOND free provider (AI_FALLBACK_*) tried when the first one
    is rate-limited or down, so a busy free quota does not stop the feature
  * tolerant JSON handling (code fences / stray text around the JSON object)
"""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.schemas.ai_bulk import CandidateBatch

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.5-flash-lite",
    "ollama": "qwen3:4b",
}
MAX_RETRY_WAIT_SECONDS = 8.0
_FALLBACK_STATUSES = {429, 502, 503}


def _safe_token(value: object) -> str:
    """Log only short provider error identifiers, never prompts or API tokens."""
    if not isinstance(value, (str, int)):
        return "unknown"
    value = str(value)
    return value[:80] if re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value) else "unknown"


def _report_provider_http_error(provider: str, model: str, status_code: int,
                                body: object = None, request_id: object = None) -> None:
    error = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    logger.error(
        "AI request rejected provider=%s model=%s provider_http=%s error_code=%s "
        "error_type=%s request_id=%s (no transaction text/API key logged)",
        provider, model, status_code,
        _safe_token(error.get("code")),
        _safe_token(error.get("type")),
        _safe_token(request_id),
    )


def _public_provider_error(provider: str, code: int) -> HTTPException:
    """Give the browser a helpful category without exposing provider payloads."""
    name = {"groq": "Groq", "gemini": "Gemini", "ollama": "Ollama"}.get(provider, "AI provider")
    if code == 400:
        return HTTPException(502, f"{name} rejected the request format (HTTP 400). Check structured-output schema and model support; no transactions were posted.")
    if code == 401:
        return HTTPException(503, f"{name} rejected the API credentials (HTTP 401). Check the configured provider key; no transactions were posted.")
    if code == 403:
        return HTTPException(503, f"{name} denied access (HTTP 403). Check API-key permissions, account status, and model access; no transactions were posted.")
    if code == 404:
        return HTTPException(503, f"{name} model/endpoint was not found (HTTP 404). Check the configured model ID; no transactions were posted.")
    if code == 413:
        return HTTPException(413, f"{name} rejected a large request. Try fewer lines; no transactions were posted.")
    if code == 429:
        return HTTPException(429, f"{name} free-tier quota/rate limit reached (HTTP 429). Try later or use fewer lines; no transactions were posted.")
    if code >= 500:
        return HTTPException(503, f"{name} is temporarily failing (HTTP {code}). Try again later; no transactions were posted.")
    return HTTPException(502, f"{name} request failed (provider HTTP {code}). Check backend AI logs; no transactions were posted.")


class _Provider:
    """One concrete provider selection (name + key + model)."""
    def __init__(self, name: str, api_key: str, model: str):
        self.name, self.api_key, self.model = name, api_key, model


def provider_name() -> str:
    provider = settings.AI_PROVIDER.strip().lower()
    if provider not in DEFAULT_MODELS:
        raise HTTPException(503, "AI_PROVIDER must be groq, gemini, or ollama.")
    return provider


def model_name() -> str:
    return settings.AI_MODEL.strip() or DEFAULT_MODELS[provider_name()]


def _primary() -> _Provider:
    return _Provider(provider_name(), settings.AI_API_KEY, model_name())


def _fallback() -> _Provider | None:
    name = (getattr(settings, "AI_FALLBACK_PROVIDER", "") or "").strip().lower()
    if not name:
        return None
    if name not in DEFAULT_MODELS:
        logger.warning("AI_FALLBACK_PROVIDER ignored: must be groq, gemini or ollama")
        return None
    key = getattr(settings, "AI_FALLBACK_API_KEY", "") or ""
    if name != "ollama" and not key:
        return None
    model = (getattr(settings, "AI_FALLBACK_MODEL", "") or "").strip() or DEFAULT_MODELS[name]
    return _Provider(name, key, model)


def public_provider_info() -> dict:
    provider = provider_name()
    fallback = _fallback()
    return {
        "provider": provider,
        "model": model_name(),
        "local_processing": provider == "ollama",
        "free_plan_has_limits": provider != "ollama",
        "fallback_provider": fallback.name if fallback else None,
    }


def _strict_schema(model: type[BaseModel] = CandidateBatch) -> dict:
    # Strict Groq JSON-schema mode needs every object field required and
    # additionalProperties:false, including objects referenced in $defs.
    # "default" keys are removed: strict mode rejects them, and our validators
    # fill missing optional keys anyway.
    schema = model.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema


def _extract_json(content: str) -> str:
    """Tolerate ```json fences or chatter around the object some small models add."""
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{") and "{" in text and "}" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    return text


def _validate_json(content: str, model: type[BaseModel] = CandidateBatch):
    if not content:
        raise HTTPException(502, "AI returned an empty response. Nothing was posted.")
    try:
        result = model.model_validate_json(_extract_json(content))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(502, "AI response could not be validated. No transactions were posted.") from exc
    if isinstance(result, CandidateBatch) and len(result.transactions) > 400:
        raise HTTPException(502, "AI returned too many entries. Nothing was posted.")
    return result


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(exc, "headers", None)
    try:
        value = headers.get("retry-after") if headers else None
        return float(value) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _openai_compatible(system_prompt: str, user_prompt: str, provider: str,
                       model: type[BaseModel] = CandidateBatch, *, api_key: str | None = None,
                       model_id: str | None = None, schema_name: str = "sfms_candidate_batch"):
    from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError
    model_id = model_id or (model_name() if provider == provider_name() else DEFAULT_MODELS[provider])
    if provider == "groq":
        api_key = api_key if api_key is not None else settings.AI_API_KEY
        if not api_key:
            raise HTTPException(503, "Groq free API key missing. Configure AI_API_KEY in the backend .env.")
        base_url = "https://api.groq.com/openai/v1"
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": _strict_schema(model)},
        }
        timeout = 60.0
    else:
        base_url = settings.AI_OLLAMA_BASE_URL.rstrip("/")
        if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
            raise HTTPException(503, "Local Ollama URL must be localhost. Remote Ollama is disabled.")
        api_key = "ollama"
        response_format = {"type": "json_object"}
        timeout = 120.0
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_format,
            temperature=0,
        )
        return _validate_json(response.choices[0].message.content or "", model)
    except HTTPException:
        raise
    except APIStatusError as exc:
        request_id = getattr(exc, 'request_id', None)
        _report_provider_http_error(provider, model_id, exc.status_code,
                                    getattr(exc, 'body', None), request_id)
        public = _public_provider_error(provider, exc.status_code)
        public.retry_after = _retry_after(exc)  # type: ignore[attr-defined]
        raise public from exc
    except (APITimeoutError, APIConnectionError) as exc:
        logger.warning("AI connectivity failed provider=%s model=%s error=%s",
                       provider, model_id, type(exc).__name__)
        raise HTTPException(503, "AI provider is unreachable or timed out. No transactions were posted.") from exc
    except Exception as exc:
        logger.error("AI unexpected failure provider=%s model=%s exception_type=%s",
                     provider, model_id, type(exc).__name__)
        raise HTTPException(502, "AI parsing failed. Check backend AI logs; no transactions were posted.") from exc


def _gemini(system_prompt: str, user_prompt: str, model: type[BaseModel] = CandidateBatch, *,
            api_key: str | None = None, model_id: str | None = None):
    api_key = api_key if api_key is not None else settings.AI_API_KEY
    if not api_key:
        raise HTTPException(503, "Gemini free-tier API key missing. Configure AI_API_KEY in the backend .env.")
    model_id = model_id or (model_name() if provider_name() == "gemini" else DEFAULT_MODELS["gemini"])
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(model_id, safe="-._") + ":generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": model.model_json_schema(),
        },
    }
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                      method="POST")
    try:
        with urlopen(request, timeout=65) as response:
            body = json.load(response)
        parts = body["candidates"][0]["content"]["parts"]
        content = "".join(p.get("text", "") for p in parts)
        return _validate_json(content, model)
    except HTTPError as exc:
        # Do not log the provider's full body: it could include private text.
        error_body = None
        try:
            error_body = json.loads(exc.read(16384).decode('utf-8'))
        except (ValueError, UnicodeDecodeError, OSError):
            pass
        _report_provider_http_error('gemini', model_id, exc.code, error_body,
                                    exc.headers.get('x-request-id') if exc.headers else None)
        public = _public_provider_error('gemini', exc.code)
        public.retry_after = _retry_after(exc)  # type: ignore[attr-defined]
        raise public from exc
    except HTTPException:
        raise
    except (URLError, TimeoutError) as exc:
        logger.warning("AI connectivity failed provider=gemini model=%s error=%s", model_id, type(exc).__name__)
        raise HTTPException(503, "Gemini API could not be reached. No transactions were posted.") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.error("AI incomplete response provider=gemini model=%s error=%s", model_id, type(exc).__name__)
        raise HTTPException(502, "Gemini output is incomplete. No transactions were posted.") from exc


def _call_provider(selected: _Provider, system_prompt: str, user_prompt: str,
                   model: type[BaseModel], schema_name: str):
    if selected.name == "gemini":
        return _gemini(system_prompt, user_prompt, model, api_key=selected.api_key, model_id=selected.model)
    return _openai_compatible(system_prompt, user_prompt, selected.name, model,
                              api_key=selected.api_key, model_id=selected.model, schema_name=schema_name)


def _call_with_retry(selected: _Provider, system_prompt: str, user_prompt: str,
                     model: type[BaseModel], schema_name: str):
    try:
        return _call_provider(selected, system_prompt, user_prompt, model, schema_name)
    except HTTPException as exc:
        wait = getattr(exc, "retry_after", None)
        # One polite retry only when the provider says the wait is short.
        if exc.status_code in (429, 503) and wait is not None and 0 < wait <= MAX_RETRY_WAIT_SECONDS:
            time.sleep(wait)
            return _call_provider(selected, system_prompt, user_prompt, model, schema_name)
        raise


def call_structured(system_prompt: str, user_prompt: str, model: type[BaseModel],
                    schema_name: str = "sfms_structured"):
    """Run one structured-output call on the primary provider, then the fallback.

    The fallback is tried only for rate-limit/outage style failures, never for
    a rejected request or bad credentials (those would just fail again).
    """
    primary = _primary()
    try:
        return _call_with_retry(primary, system_prompt, user_prompt, model, schema_name)
    except HTTPException as exc:
        fallback = _fallback()
        if fallback is None or exc.status_code not in _FALLBACK_STATUSES:
            raise
        logger.warning("AI primary provider=%s failed (HTTP %s); trying fallback provider=%s",
                       primary.name, exc.status_code, fallback.name)
        return _call_with_retry(fallback, system_prompt, user_prompt, model, schema_name)


def _stated_prefix_amount(source: str):
    """Return a Decimal only when the transaction uses an explicit AMOUNT(...) prefix."""
    from decimal import Decimal, InvalidOperation
    m = re.match(r'^\s*(?:(?:rs\.?|pkr)\s*)?(\d[\d,]*(?:\.\d+)?)([kK]?)\s*\(', source)
    if not m:
        return None
    try:
        amount = Decimal(m.group(1).replace(',', '')) * (1000 if m.group(2) else 1)
        return amount if amount.is_finite() and amount > 0 else None
    except InvalidOperation:
        return None


def _coverage_issues(user_prompt: str, batch: CandidateBatch) -> set[int]:
    """Check observable extraction coverage, not subjective model confidence.

    The numbered-line protocol is authored by SFMS, so these IDs are trusted.
    A clearly present leading amount must not be silently dropped; other ambiguities
    continue to the existing question/review flow instead of being guessed.
    """
    segments = {int(n): text for n, text in re.findall(r"(?m)^\[(\d+)\]\s*(.*)$", user_prompt)}
    if not segments:
        return set()  # Older callers and isolated provider tests have no numbered lines.
    got: dict[int, list] = {}
    for candidate in batch.transactions:
        if candidate.source_line in segments:
            got.setdefault(candidate.source_line, []).append(candidate)
    missing = set(segments) - set(got)
    for number, source in segments.items():
        # Conservative: only an unambiguous monetary prefix, not a count/date.
        leading_amount = _stated_prefix_amount(source)
        if leading_amount is not None and not any(x.amount is not None for x in got.get(number, [])):
            missing.add(number)
    return missing


def parse_candidate_batch(system_prompt: str, user_prompt: str) -> CandidateBatch:
    # Existing provider outage/quota fallback remains inside call_structured.
    result = call_structured(system_prompt, user_prompt, CandidateBatch, "sfms_candidate_batch")
    if not result.transactions:
        # A blank but syntactically valid response is not enough to create drafts.
        # Try the configured secondary provider once, otherwise let the planner
        # create clearly labelled basic extraction drafts.
        secondary = _fallback()
        if secondary is not None and secondary.name != provider_name():
            try:
                replacement = _call_with_retry(secondary, system_prompt, user_prompt, CandidateBatch, 'sfms_candidate_batch')
                return replacement if replacement.transactions else result
            except HTTPException:
                logger.warning('AI secondary extraction returned an error; keeping original empty result')
        return result
    if not getattr(settings, 'AI_REVIEW_INCOMPLETE_PRIMARY', True):
        return result
    gaps = _coverage_issues(user_prompt, result)
    fallback = _fallback()
    if not gaps or fallback is None or fallback.name == provider_name():
        return result
    try:
        second = _call_with_retry(fallback, system_prompt, user_prompt, CandidateBatch, 'sfms_candidate_batch')
    except HTTPException as exc:
        logger.warning('AI extraction completeness review unavailable (HTTP %s); keeping first response', exc.status_code)
        return result
    # Never replace grounded entries on confidence scores alone; only add missing
    # numbered segments supplied by the secondary provider, preserving originals.
    originals = {int(n): text for n, text in re.findall(r"(?m)^\[(\d+)\]\s*(.*)$", user_prompt)}
    from decimal import Decimal, InvalidOperation
    def grounded_amount(c):
        explicit = _stated_prefix_amount(originals.get(c.source_line, ''))
        if explicit is None:
            return True  # No clear numeric prefix: normal review engine still validates it.
        try:
            return c.amount is not None and Decimal(c.amount.replace(',', '')) == explicit
        except (InvalidOperation, AttributeError, TypeError):
            return False

    second_for_gap = [c for c in second.transactions if c.source_line in gaps and c.source_line not in
                      {c0.source_line for c0 in result.transactions} and grounded_amount(c)]
    if second_for_gap:
        result = result.model_copy(update={'transactions': result.transactions + second_for_gap})
    # For same-source candidates where the primary omitted an obvious leading
    # amount, accept the secondary only if it supplies that amount, but leave the
    # rest of the original fields untouched. Final user confirmation is mandatory.
    amendments = []
    for first in result.transactions:
        if first.source_line not in gaps or first.amount is not None:
            amendments.append(first)
            continue
        second_amounts = [c.amount for c in second.transactions if c.source_line == first.source_line and c.amount is not None]
        if len(set(second_amounts)) == 1 and grounded_amount(first.model_copy(update={'amount': second_amounts[0]})):
            amendments.append(first.model_copy(update={'amount': second_amounts[0],
                  'warnings': list(first.warnings) + ['Secondary AI suggested amount; verify before posting.']}))
        else:
            amendments.append(first)
    return result.model_copy(update={'transactions': amendments})
