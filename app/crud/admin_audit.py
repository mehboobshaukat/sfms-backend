"""Append-only audit trail for admin actions (metadata only, no financial data)."""
from sqlalchemy.orm import Session

from app.models.admin_action import AdminAction
from app.models.user import User


def record(
    db: Session, actor: User | None, action: str, target_type: str, target_ref: str,
    detail: str | None = None, ip: str | None = None,
) -> None:
    """Adds an audit row to the CURRENT transaction (caller commits, so the action and
    its audit record succeed or fail together)."""
    db.add(AdminAction(
        actor_id=actor.id if actor else None,
        actor_label=(actor.username if actor else "cli")[:160],
        action=action[:40],
        target_type=target_type[:30],
        target_ref=target_ref[:100],
        detail=detail[:500] if detail else None,
        ip=(ip or None) and ip[:45],
    ))
