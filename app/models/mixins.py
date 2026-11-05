from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SlugMixin:
    """
    Public identifier for anything exposed over the API — URLs, request/
    response bodies, and AI model input/output all use this, NEVER the raw
    integer `id`. Format: "{entity_prefix}-{12 hex chars}", e.g.
    "cat-a8fe7a1bda05" — no user_id embedded (keeps the id itself
    unexposed, per the no-raw-ids policy). Immutable once set — generated
    once at creation (see app/utils/slugs.py) and never regenerated on
    rename, so anything that saved a slug (an AI draft, a mobile cache,
    a bookmarked URL) keeps working even after the record is renamed.
    """
    slug: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
