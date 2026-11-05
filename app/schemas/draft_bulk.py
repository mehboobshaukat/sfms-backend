"""Explicitly approved bulk draft actions; each real transaction retains its own ID/code."""
from pydantic import BaseModel, Field, model_validator


class BulkDraftSelection(BaseModel):
    # UI currently selects on one page. Bound batches for safe request execution.
    draft_slugs: list[str] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_slugs(self):
        cleaned = [item.strip() for item in self.draft_slugs]
        if any(not item or len(item) > 100 for item in cleaned):
            raise ValueError("All draft identifiers must be non-empty and at most 100 characters.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate draft identifiers are not allowed.")
        self.draft_slugs = cleaned
        return self


class BulkPostedTransaction(BaseModel):
    draft_slug: str
    transaction_code: str


class BulkDraftConfirmResult(BaseModel):
    count: int
    transactions: list[BulkPostedTransaction]


class BulkDraftRejectResult(BaseModel):
    count: int
    rejected_slugs: list[str]
