"""
Shared helpers for the two system-wide rules agreed in the backend
redesign:

1. Every externally-exposed entity gets an immutable, opaque `slug`
   ("{entity_prefix}-{12 hex chars}") instead of exposing its raw
   integer `id`. Transaction and Loan are the two exceptions — they
   already have their own human-readable unique codes
   (transaction_code / loan_code) which serve exactly the same purpose,
   so they don't get a second slug field.

2. Certain lookup/reference entities (see NAME_UNIQUE_ENTITIES below)
   must have a unique name per user, compared case-insensitively and
   trimmed — e.g. "Family", "family", " Family " are all the same name.
   This does NOT apply to transactional/record entities (Transaction,
   CreditPurchase, Budget's own records, Goal) where repeated or
   similar names are normal and expected.
"""
import secrets

from sqlalchemy import select, func
from sqlalchemy.orm import Session

# Keep these short and stable — they're baked into every slug ever issued.
ENTITY_PREFIXES = {
    "category": "cat",
    "group": "grp",
    "contact": "con",
    "transaction_type": "tt",
    "priority": "pr",
    "account": "acc",
    "payment_method": "pm",
    "budget": "bud",
    "goal": "gl",
    "credit_purchase": "cp",
    "transaction_draft": "draft",
    "ai_bulk_session": "aib",
    "history_entry": "hist",
    "contact_message": "msg",
}

# Entities where a duplicate (case/whitespace-insensitive) name is
# rejected outright, forcing the user to pick something distinguishable.
# Deliberately excludes Transaction (same description recurs constantly
# across different days), CreditPurchase (same item bought more than
# once), Budget and Goal (informally-reused names, e.g. re-running an
# "Emergency Fund" goal next year, are normal).
NAME_UNIQUE_ENTITIES = {
    "category",
    "group",
    "contact",
    "priority",
    "account",
    "transaction_type",
    "payment_method",
}


def generate_slug(db: Session, model, entity_key: str) -> str:
    """
    Generates a unique "{prefix}-{12 hex chars}" slug for `model`,
    verifying against the database before returning — collisions are
    astronomically unlikely at 12 hex chars (48 bits) but the DB has the
    final word regardless, so we check rather than assume.
    """
    prefix = ENTITY_PREFIXES[entity_key]
    for _ in range(10):  # effectively always succeeds on the first try
        candidate = f"{prefix}-{secrets.token_hex(6)}"  # 6 bytes = 12 hex chars
        exists = db.execute(select(model.id).where(model.slug == candidate)).scalar_one_or_none()
        if exists is None:
            return candidate
    raise RuntimeError(f"Could not generate a unique slug for {entity_key} after 10 attempts.")


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).lower()


def assert_unique_name(db: Session, model, user_id: int, name: str, entity_key: str, exclude_id: int | None = None) -> None:
    """
    Raises HTTPException(409) if `name` collides (case/whitespace-
    insensitively) with an existing row of `model` owned by `user_id`.
    Only call this for entity_keys in NAME_UNIQUE_ENTITIES — callers
    decide that, this function just enforces it once called.
    `exclude_id` lets an update skip comparing a row against itself.
    """
    from fastapi import HTTPException, status

    normalized = normalize_name(name)
    stmt = select(model.id).where(
        model.user_id == user_id,
        func.lower(func.trim(model.name)) == normalized,
    )
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    clash = db.execute(stmt).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {entity_key.replace('_', ' ')} named '{name.strip()}' already exists. Choose a different name.",
        )
