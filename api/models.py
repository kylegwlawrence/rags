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


class Article(BaseModel):
    """One row from `simplewiki.articles`. Wikitext body lives at /content."""

    page_id: int
    title: str
    namespace: int
    revision_id: int
    timestamp: str
    text_bytes: int | None


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


class PydocsDoc(BaseModel):
    """One row from `python_docs.docs`. Raw body lives at /content.

    `content_chars` is `length(content)` — SQLite returns the character count
    for a TEXT value, not its UTF-8 byte length, so this is named for what it
    actually measures.
    """

    doc_path: str
    section: str | None
    title: str | None
    content_chars: int | None


class WikihowArticle(BaseModel):
    """One row from `wikihow.articles` — a single step of a how-to guide.

    Several rows share a `title` (one per step); `/wikihow/chunks` reassembles
    whole guides for retrieval. Raw step `text` lives at /content.
    """

    id: int
    title: str | None
    section_label: str | None
    headline: str | None
    text_chars: int | None


class StoredChunk(BaseModel):
    """One chunk row from `<source>_rag.db`, fetched by doc_id for inspection."""

    chunk_id: int
    doc_id: str
    section: str | None
    chunk_index: int
    text: str
    text_length: int


class Chunk(BaseModel):
    """One retrieved chunk from a `<source>_rag.db` hybrid search."""

    chunk_id: int
    doc_id: str
    title: str
    section: str | None
    chunk_index: int
    text: str
    text_length: int
    score: float


class ChunksResponse(BaseModel):
    """Hybrid-search response. Not a `Page[Chunk]` — RRF doesn't paginate."""

    items: list[Chunk]
    used_dense: bool
    top_k: int
    candidate_k: int
