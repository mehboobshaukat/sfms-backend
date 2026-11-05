from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    # Which category(ies) to link this group under right away — same
    # reuse pattern as Category.transaction_type_slugs.
    category_slugs: list[str] = Field(default_factory=list)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    category_slugs: list[str]
    category_labels: dict[str, str] = Field(default_factory=dict)
    contact_slugs: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime
