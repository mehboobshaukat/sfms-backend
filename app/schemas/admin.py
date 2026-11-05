"""Schemas for the admin layer and the public contact form.

Admin responses are explicit whitelists: they never reuse the user-facing
schemas, so a new column added to User can't leak to the panel by accident.
No financial data and no raw integer ids appear here (users are addressed by username).
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _single_line(value: str) -> str:
    return " ".join(_CONTROL_CHARS.sub("", value).split())


def _multi_line(value: str) -> str:
    return _CONTROL_CHARS.sub("", value).replace("\r\n", "\n").replace("\r", "\n").strip()


MessageStatus = Literal["new", "in_progress", "resolved"]
MessageView = Literal["inbox", "archived", "trash", "all"]
BulkAction = Literal["read", "unread", "archive", "unarchive", "delete", "restore"]
UserStatus = Literal["active", "unverified", "blocked", "locked", "pending_deletion"]


# ---------------- public contact form ----------------
class ContactSubmit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=10, max_length=5000)
    # Honeypot: hidden field real users never fill in. Bots usually do.
    website: str = Field(default="", max_length=200)

    @field_validator("name", "subject")
    @classmethod
    def _clean_single(cls, v: str) -> str:
        v = _single_line(v)
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("message")
    @classmethod
    def _clean_message(cls, v: str) -> str:
        v = _multi_line(v)
        if len(v) < 10:
            raise ValueError("message is too short")
        return v


# ---------------- contact messages (admin) ----------------
class ReplyOut(BaseModel):
    admin_name: str
    subject: str
    body: str
    created_at: datetime


class MessageSummaryOut(BaseModel):
    slug: str
    name: str
    email: str
    subject: str
    preview: str
    status: MessageStatus
    is_read: bool
    is_archived: bool
    is_trashed: bool
    reply_count: int
    created_at: datetime


class MessageDetailOut(BaseModel):
    slug: str
    name: str
    email: str
    subject: str
    message: str  # plain text ONLY - clients must never render it as HTML
    status: MessageStatus
    is_read: bool
    is_archived: bool
    is_trashed: bool
    deleted_at: datetime | None
    created_at: datetime
    replies: list[ReplyOut]


class MessageUpdate(BaseModel):
    is_read: bool | None = None
    status: MessageStatus | None = None
    is_archived: bool | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.is_read is None and self.status is None and self.is_archived is None:
            raise ValueError("Provide at least one of is_read, status, is_archived.")
        return self


class MessageBulk(BaseModel):
    slugs: list[str] = Field(min_length=1, max_length=100)
    action: BulkAction


class ReplyCreate(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    mark_resolved: bool = False

    @field_validator("subject")
    @classmethod
    def _subject(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = _single_line(v)
        return v or None

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        v = _multi_line(v)
        if not v:
            raise ValueError("Reply must not be empty.")
        return v


class MessageStatsOut(BaseModel):
    unread: int
    new: int
    in_progress: int
    resolved: int
    archived: int
    trash: int


# ---------------- users (admin) ----------------
class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str
    email: str
    first_name: str | None
    last_name: str | None
    country: str | None
    country_name: str | None
    role: str
    status: UserStatus
    email_confirmed: bool
    is_blocked: bool
    blocked_at: datetime | None
    blocked_reason: str | None
    locked_until: datetime | None
    deletion_scheduled_at: datetime | None
    created_at: datetime


class BlockUserRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        v = _single_line(v)
        if len(v) < 3:
            raise ValueError("Please give a short reason.")
        return v


class ConfirmPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PromoteAdminRequest(ConfirmPasswordRequest):
    username: str = Field(min_length=1, max_length=50)


class AdminMeOut(BaseModel):
    username: str
    email: str
    first_name: str | None
    last_name: str | None
    role: str
    capabilities: dict[str, bool]
