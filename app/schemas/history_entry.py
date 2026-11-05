from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HistoryEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    entity_type: str
    entity_slug: str
    entity_label: str
    action: str
    performed_by: str
    is_undone: bool
    undone_at: datetime | None
    expires_at: datetime
    created_at: datetime
