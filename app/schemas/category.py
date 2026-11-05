from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    # Which transaction type(s) to link this category under right away.
    # Can be left empty and linked later via the link endpoint — this is
    # what makes a category reusable across types (Option B): pass the
    # slug of an EXISTING category you already have (from a different
    # type) here to link it to a new type instead of creating a duplicate.
    transaction_type_slugs: list[str] = Field(default_factory=list)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    is_active: bool | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    icon: str | None
    color: str | None
    transaction_type_slugs: list[str]
    transaction_type_labels: dict[str, str] = Field(default_factory=dict)
    group_slugs: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
