from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class CountrySummary(BaseModel):
    id: str
    name: str | None
    region: str | None


class CountryDetail(CountrySummary):
    data: dict | list | None


class Work(BaseModel):
    id: str
    openalex_url: str
    title: str | None
    abstract: str | None
    year: int | None
    cited_by_count: int | None
    doi: str | None
    authors: list[str]
    venue: str | None


class GutenbergText(BaseModel):
    id: int
    title: str | None
    author: str | None
    language: str | None
    release_date: str | None
    size_bytes: int | None
    path: str


class Paper(BaseModel):
    id: str
    title: str
    abstract: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    submitted_date: str
    updated_date: str | None
    doi: str | None
    journal_ref: str | None
    comments: str | None
    has_html: bool
