from datetime import datetime

from sqlalchemy import String, Boolean, Integer, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class RefreshToken(Base, TimestampMixin):
    """
    Whitelist/blacklist table for refresh tokens.
    Each issued refresh token gets a row here (jti = unique token id).
    On refresh, the old row is revoked and a new one is inserted (rotation).
    On logout, the row is revoked.
    """
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user = relationship("User", back_populates="refresh_tokens")
