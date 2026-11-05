"""
Make an EXISTING account the (single) super admin. There is deliberately no API for this.

    python -m app.utils.create_super_admin you@example.com
    python -m app.utils.create_super_admin new-owner@example.com --transfer

Register the account normally first (and verify its email); this script only changes its role.
If a super admin already exists, --transfer demotes them to a regular admin.
"""
import argparse
import sys

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core.roles import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.crud import admin_audit
from app.models.user import User


def run(email: str, transfer: bool = False, assume_yes: bool = False) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
        if user is None:
            print(f"No account with email {email!r}. Register it first.")
            return 1
        if user.is_deleted or user.is_blocked or not user.email_confirmed:
            print("The account must be active and email-verified (not blocked or pending deletion).")
            return 1

        current = db.query(User).filter(User.role == ROLE_SUPER_ADMIN).first()
        if current is not None and current.id == user.id:
            print(f"{user.username} is already the super admin.")
            return 0
        if current is not None and not transfer:
            print(f"A super admin already exists ({current.username}). Use --transfer to hand over ownership.")
            return 1

        if not assume_yes:
            action = f"make {user.username} <{user.email}> the super admin"
            if current is not None:
                action += f" and demote {current.username} to admin"
            if input(f"About to {action}. Type 'yes' to continue: ").strip().lower() != "yes":
                print("Cancelled.")
                return 1

        if current is not None:
            current.role = ROLE_ADMIN
            db.flush()  # free the single-super-admin slot before the next UPDATE
        user.role = ROLE_SUPER_ADMIN
        admin_audit.record(db, None, "admin.set_super_admin", "user", user.username,
                           detail=f"transferred from {current.username}" if current else None)
        db.commit()
        print(f"{user.username} is now the super admin.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--transfer", action="store_true", help="demote the current super admin to admin")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    sys.exit(run(args.email, args.transfer, args.yes))
