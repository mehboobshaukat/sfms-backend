from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from app.utils.geography import require_country


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,49}$")
_PHONE_ALLOWED_RE = re.compile(r"^[+0-9()\-\s]+$")


def _validate_username(value: str) -> str:
    # Do not silently trim spaces: usernames containing any whitespace must be
    # rejected exactly as requested. New usernames are normalized to lowercase
    # so uniqueness is predictable to users.
    if not value or value != value.strip() or any(ch.isspace() for ch in value):
        raise ValueError("Username cannot contain spaces.")
    if not _USERNAME_RE.fullmatch(value):
        raise ValueError(
            "Username must start with a letter and contain only letters, numbers, and underscores."
        )
    return value.lower()


def _validate_person_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise ValueError("Name cannot be empty.")
    if len(cleaned) > 80:
        raise ValueError("Name must be 80 characters or fewer.")
    if not all(ch.isalpha() or ch in {" ", "-", "'", "’"} for ch in cleaned):
        raise ValueError("Name can contain letters, spaces, apostrophes, and hyphens only.")
    return cleaned


def _normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if not _PHONE_ALLOWED_RE.fullmatch(raw):
        raise ValueError("Phone number contains unsupported characters.")

    compact = re.sub(r"[()\-\s]", "", raw)
    if compact.startswith("00"):
        compact = "+" + compact[2:]

    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise ValueError("Phone number must contain between 7 and 15 digits.")
    return compact


def _normalize_location(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.strip().split()) or None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone_number: str | None = Field(default=None, max_length=30)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code, e.g. PK")
    state: str = Field(min_length=1, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _validate_username(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_names(cls, value: str) -> str:
        return _validate_person_name(value)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value)


    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        return require_country(value)

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        cleaned = _normalize_location(value)
        if not cleaned:
            raise ValueError("State / province is required.")
        return cleaned

    @field_validator("city", "postal_code")
    @classmethod
    def validate_location_fields(cls, value: str | None) -> str | None:
        return _normalize_location(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(BaseModel):
    # "scheduled" (default) keeps the existing 30-day recovery grace period.
    # "instant" permanently erases the account and all its data right away.
    deletion_type: Literal["scheduled", "instant"] = "scheduled"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    first_name: str | None = None
    last_name: str | None = None
    email: str
    phone_number: str | None = None
    profile_image_url: str | None = None
    country: str | None = None
    country_name: str | None = None
    state: str | None = None
    city: str | None = None
    postal_code: str | None = None
    currency_code: str
    currency_symbol: str
    role: str
    email_confirmed: bool
    is_blocked: bool
    created_at: datetime
    is_deleted: bool = False
    deletion_scheduled_at: datetime | None = None


class UserUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=50)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    phone_number: str | None = Field(default=None, max_length=30)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    state: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str | None) -> str | None:
        return require_country(value) if value is not None else None

    @field_validator("state", "city", "postal_code")
    @classmethod
    def validate_location_fields(cls, value: str | None) -> str | None:
        return _normalize_location(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        return _validate_username(value) if value is not None else None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_names(cls, value: str | None) -> str | None:
        return _validate_person_name(value) if value is not None else None

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value)
