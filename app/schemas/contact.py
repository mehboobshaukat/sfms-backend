from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, EmailStr


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    notes: str | None = None
    # Which group(s) to link this contact under right away — same reuse
    # pattern as Category/Group.
    group_slugs: list[str] = Field(default_factory=list)


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    group_slugs: list[str]
    group_labels: dict[str, str] = Field(default_factory=dict)
    is_active: bool
    created_at: datetime
    updated_at: datetime
