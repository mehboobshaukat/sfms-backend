"""
Standalone cron entrypoint: hard-deletes every account whose 30-day
scheduled-deletion window has passed.

This is the "instant vs 30 days" feature's other half — choosing
"scheduled" at delete time only sets a timer; something still has to
actually finish the job when that timer runs out. Wire this up as a
daily cron job (recommended) rather than relying solely on the
best-effort purge that also runs once at app startup (app/main.py):

    # crontab -e
    0 3 * * * cd /path/to/sfms-backend && /path/to/venv/bin/python -m app.utils.purge_deleted_users >> /var/log/sfms_purge.log 2>&1

Or as a one-off:
    python -m app.utils.purge_deleted_users
"""
from app.core.database import SessionLocal
from app.crud.auth_service import purge_due_deletions


def run():
    db = SessionLocal()
    try:
        count = purge_due_deletions(db)
        print(f"Purged {count} account(s) past their scheduled deletion date.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
