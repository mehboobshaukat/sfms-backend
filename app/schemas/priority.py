from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


class PriorityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class PriorityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    is_active: bool | None = None


class PriorityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
