"""Shared response shapes."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

#: Bounds for every paginated list endpoint. 200 because the Team page asks
#: for 200 users in one go; nothing else asks for more than 50.
MAX_PAGE_SIZE = 200
#: Past this an offset scan is pointless work; nobody pages 10,000 deep.
MAX_PAGE = 10_000
#: Hard cap for the few endpoints that return a bare list (no Page envelope).
MAX_LIST_ITEMS = 1_000


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """The envelope every list endpoint returns."""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class Message(BaseModel):
    """For endpoints whose only useful answer is "that worked"."""

    message: str
