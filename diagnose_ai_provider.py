"""Safe live AI connectivity probe.

Run from backend folder with its .env loaded: python diagnose_ai_provider.py
Uses synthetic (non-financial, non-personal) data. Does not write to the DB.
The provider's error message may contain request diagnostics; inspect it locally
before sharing output publicly. This probe may use free-tier request quota.
"""
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.core.config import settings
from app.ai.providers import _strict_schema, model_name, provider_name

SYSTEM = (
    "You extract financial transactions from short text for a test. "
    "Return a JSON object with transactions. Each transaction needs all fields "
    "in the schema. Use null for unknown optional facts and [] for warnings."
)
USER = "[1] Demo only: paid 10 rupees for a pencil."


def redacted(message):
    message = str(message or "")
    if settings.AI_API_KEY:
        message = message.replace(settings.AI_API_KEY, '[REDACTED_KEY]')
    message = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_.-]+', r'\1[REDACTED]', message)
    return message[:1200]


def describe_http_error(code, error_body):
    error = error_body.get('error', error_body) if isinstance(error_body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    print(f"PROVIDER HTTP STATUS: {code}")
    print(f"ERROR CODE: {redacted(error.get('code', 'unknown'))}")
    print(f"ERROR TYPE: {redacted(error.get('type', 'unknown'))}")
    print(f"ERROR MESSAGE: {redacted(error.get('message', 'Unavailable'))}")
    print('Do not paste your API key or real transaction text into a public report.')


def main():
    provider = provider_name()
    model = model_name()
    print(f"Provider={provider}, Model={model}")
    print('Sending one synthetic request: no real user data or DB writes.')
    if provider in ('groq', 'ollama'):
        try:
            from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError
        except ImportError:
            print('Missing SDK; run python -m pip install -r requirements.txt')
            return 2
        if provider == 'groq' and not settings.AI_API_KEY:
            print('Groq API key missing from backend .env (AI_API_KEY).')
            return 2
        base_url = 'https://api.groq.com/openai/v1' if provider == 'groq' else settings.AI_OLLAMA_BASE_URL.rstrip('/')
        if provider == 'ollama' and not (base_url.startswith('http://127.0.0.1:') or base_url.startswith('http://localhost:')):
            print('Ollama URL must point to localhost.')
            return 2
        client = OpenAI(api_key=settings.AI_API_KEY if provider == 'groq' else 'ollama',
                        base_url=base_url, timeout=60, max_retries=0)
        response_format = ({'type':'json_schema', 'json_schema': {'name':'sfms_candidate_batch', 'strict': True,
                            'schema':_strict_schema()}} if provider == 'groq' else {'type':'json_object'})
        try:
            response = client.chat.completions.create(
                model=model, messages=[{'role':'system', 'content':SYSTEM}, {'role':'user','content':USER}],
                response_format=response_format, temperature=0,
            )
        except APIStatusError as exc:
            describe_http_error(exc.status_code, getattr(exc, 'body', None))
            return 1
        except (APIConnectionError, APITimeoutError) as exc:
            print('CONNECTIVITY ERROR:', type(exc).__name__)
            return 1
        content = response.choices[0].message.content or ''
    else:
        if not settings.AI_API_KEY:
            print('Gemini API key missing from backend .env (AI_API_KEY).')
            return 2
        endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + quote(model, safe='-._') + ':generateContent'
        data = {'systemInstruction': {'parts':[{'text':SYSTEM}]},
                'contents': [{'role':'user', 'parts':[{'text':USER}]}],
                'generationConfig': {'temperature':0, 'responseMimeType':'application/json',
                                     'responseJsonSchema':__import__('app.schemas.ai_bulk',fromlist=['CandidateBatch']).CandidateBatch.model_json_schema()}}
        request = Request(endpoint, method='POST', headers={'x-goog-api-key':settings.AI_API_KEY,
                          'Content-Type':'application/json'}, data=json.dumps(data).encode('utf-8'))
        try:
            with urlopen(request, timeout=65) as response:
                payload = json.load(response)
            content = ''.join(part.get('text','') for part in payload['candidates'][0]['content']['parts'])
        except HTTPError as exc:
            try:
                response_data = json.loads(exc.read(16384).decode('utf-8'))
            except (ValueError, UnicodeDecodeError, OSError):
                response_data = {}
            describe_http_error(exc.code, response_data)
            return 1
        except (URLError, TimeoutError, KeyError, IndexError, TypeError) as exc:
            print('GEMINI ERROR:', type(exc).__name__)
            return 1
    from app.schemas.ai_bulk import CandidateBatch
    try:
        result = CandidateBatch.model_validate_json(content)
        print(f'PROBE PASSED: schema valid, {len(result.transactions)} synthetic row(s) parsed.')
        return 0
    except Exception as exc:
        print('PROBE FAILED: Provider accepted the request but returned invalid schema:',type(exc).__name__)
        return 1

if __name__ == '__main__':
    sys.exit(main())
