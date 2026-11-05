from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    page: int
    page_size: int
    items: list[T]
