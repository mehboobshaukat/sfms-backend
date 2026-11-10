"""
SFMS Backend — FastAPI application entrypoint.

Run locally with:
    uvicorn app.main:app --reload --port 8000

This file ONLY wires things together (CORS, rate limiter, routers).
All business logic lives in app/crud/*_service.py, all endpoints in
app/api/v1/endpoints/*.py — see API_DOCUMENTATION.md for the full list.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from time import perf_counter
from sqlalchemy import text as sql_text
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.limiter import limiter
from app.api.v1.router import api_router
from app.core.database import engine, SessionLocal

app = FastAPI(
    title=settings.APP_NAME,
    description="Self Finance Management System — backend API",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)


# Optional profile-image storage. Never let it crash startup: on Vercel the
# project folder is read-only (MEDIA_ROOT points to /tmp there, see config.py).
if settings.APP_ENV != "production":
    try:
        Path(settings.MEDIA_ROOT).mkdir(
            parents=True,
            exist_ok=True
        )

        app.mount(
            settings.MEDIA_URL,
            StaticFiles(directory=settings.MEDIA_ROOT),
            name="media"
        )
    except OSError as exc:
        print(f"[startup] Media folder unavailable, skipping /media mount: {exc}")



@app.on_event("startup")
def _purge_overdue_account_deletions() -> None:
    """
    Best-effort safety net: hard-deletes any account whose 30-day
    scheduled-deletion window already passed. Previously nothing ever did
    this — `deletion_scheduled_at` was set and then just... sat there
    forever. This only catches up on app restart; for production, run
    `python -m app.utils.purge_deleted_users` as a daily cron job instead
    so it doesn't depend on how often the server happens to restart.

    Also purges expired History entries (30-day retention — the recycle
    bin/undo log) and expired Transaction Drafts (7-day retention — the
    AI staging area) the same way; see app/utils/purge_expired_records.py
    for the standalone cron version of all three.
    """
    from app.crud.auth_service import purge_due_deletions
    from app.crud.history_service import purge_expired as purge_history
    from app.crud.draft_service import purge_expired as purge_drafts
    from app.crud.contact_message_service import purge_trashed as purge_trashed_messages

    # Idempotent: inserts only the system-default transaction types /
    # priorities that are missing, so a fresh database never starts out
    # with empty dropdowns just because the seed script wasn't run by hand.
    try:
        from app.utils.seed_system_defaults import run as seed_system_defaults

        seed_system_defaults()
    except Exception as exc:
        print(f"[startup] System defaults seed failed: {exc}")

    # Existing users may pre-date per-user defaults introduced later (such as
    # default categories and the Cash payment method). Registration seeds them
    # for new users; this idempotent startup backfill adds only missing defaults
    # without deleting or replacing user-created data.
    if settings.RUN_STARTUP_USER_BACKFILL:
        try:
            from app.utils.backfill_default_categories import run as backfill_default_categories

            backfill_default_categories()
        except Exception as exc:
            # Keep startup resilient, matching the existing best-effort maintenance
            # behavior. The explicit migration / CLI helper can still be run later.
            print(f"[startup] Default user-data backfill failed: {exc}")

    db = SessionLocal()
    try:
        purged_users = purge_due_deletions(db)
        purged_history = purge_history(db)
        purged_drafts = purge_drafts(db)
        purged_messages = purge_trashed_messages(db)
        if purged_users or purged_history or purged_drafts or purged_messages:
            print(
                f"[startup] Purged {purged_users} account(s), {purged_history} history "
                f"entries, {purged_drafts} expired draft(s), {purged_messages} trashed contact message(s)."
            )
    except Exception as exc:  # never block app startup over this
        print(f"[startup] Skipped scheduled purge due to error: {exc}")
    finally:
        db.close()

# ---- Security headers ----
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    started = perf_counter()
    response = await call_next(request)
    elapsed_ms = (perf_counter() - started) * 1000
    # DevTools exposes Server-Timing per API response. Use this to compare
    # backend processing time with browser's end-to-end network time.
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if settings.DEBUG:
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # AI prompts, extracted drafts and lookup names are user-private data.
    # Neither the browser cache nor an intermediate proxy should retain them.
    if request.url.path.startswith((
        settings.API_V1_PREFIX + "/auth",
        settings.API_V1_PREFIX + "/ai/bulk",
        settings.API_V1_PREFIX + "/analytics",
        settings.API_V1_PREFIX + "/transaction-drafts",
        settings.API_V1_PREFIX + "/admin",  # personal data of other people
    )):
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.APP_ENV.lower() == "production" and request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ---- Rate limiting ----
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please try again later."})


# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Routers ----
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


@app.get("/health/layers", tags=["Health"])
def health_layers():
    """Readiness check: reports degraded when the main database is unavailable.

    Does not expose database addresses, credentials or driver exceptions.
    This is not an AI-provider health probe.
    """
    try:
        with engine.connect() as connection:
            connection.execute(sql_text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "layers": {"core": "unavailable", "ai": "ok" if settings.AI_ENABLED else "disabled", "frontend": "separate_deploy"}},
            headers={"Cache-Control": "no-store"},
        )
    return {
        "status": "healthy",
        "layers": {"core": "ok", "ai": "ok" if settings.AI_ENABLED else "disabled", "frontend": "separate_deploy"},
    }


@app.get("/db-test", include_in_schema=settings.DEBUG)
def database_test():
    """Development health check without exposing database exception details."""
    try:
        with engine.connect():
            return {"status": "success", "message": "Database connection successful"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Database connection failed"},
        )
