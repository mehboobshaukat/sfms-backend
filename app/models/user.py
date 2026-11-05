from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # user | admin | super_admin (see app/core/roles.py)

    # Registration/profile details. Nullable at the database level so existing
    # users created before this update remain valid; new registrations require
    # first_name and last_name through RegisterRequest.
    first_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    profile_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Nullable for existing users. New registrations require country and state.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    @property
    def country_name(self) -> str | None:
        from app.utils.geography import country_metadata
        data = country_metadata(self.country) if self.country else None
        return data["name"] if data else None

    @property
    def currency_code(self) -> str:
        from app.utils.geography import country_metadata, DEFAULT_CURRENCY_CODE
        data = country_metadata(self.country) if self.country else None
        return data["currency_code"] if data else DEFAULT_CURRENCY_CODE

    @property
    def currency_symbol(self) -> str:
        from app.utils.geography import country_metadata, DEFAULT_CURRENCY_SYMBOL
        data = country_metadata(self.country) if self.country else None
        return data["currency_symbol"] if data else DEFAULT_CURRENCY_SYMBOL

    email_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reset_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reset_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    email_verify_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verify_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # relationships (all cascade-delete on user removal is intentionally NOT set —
    # soft delete pattern is used across the app instead)
    accounts = relationship("Account", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
