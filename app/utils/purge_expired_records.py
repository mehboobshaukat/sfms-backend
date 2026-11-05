"""
Standalone cron entrypoint for the two newer retention windows added in
the backend redesign (30-day History/recycle-bin, 7-day AI drafts).
Recommended alongside purge_deleted_users.py in the same daily cron job:

    0 3 * * * cd /path/to/sfms-backend && /path/to/venv/bin/python -m app.utils.purge_expired_records >> /var/log/sfms_purge.log 2>&1

Or as a one-off:
    python -m app.utils.purge_expired_records
"""
from app.core.database import SessionLocal
from app.crud.history_service import purge_expired as purge_history
from app.crud.draft_service import purge_expired as purge_drafts
from app.ai.session import purge_expired_sessions
from app.crud.contact_message_service import purge_trashed as purge_trashed_messages


def run():
    db = SessionLocal()
    try:
        history_count = purge_history(db)
        draft_count = purge_drafts(db)
        session_count = purge_expired_sessions(db)
        message_count = purge_trashed_messages(db)
        print(f"Purged {history_count} history entr{'y' if history_count == 1 else 'ies'}, marked {draft_count} draft(s) expired, deleted {session_count} unfinished AI import session(s), {message_count} trashed contact message(s).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
