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
    # Final target page_id when this article is a #REDIRECT stub, else None.
    # Only the detail endpoint resolves this; list rows leave it None.
    redirect_to: int | None = None


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


class EmbedResult(BaseModel):
    """Result of a live single-document embed (`POST .../{id}/embed`).

    `chunk_count` is the number of chunks written; `embedded` is False when the
    document yielded no chunks (e.g. a redirect or empty body), in which case
    any previously-stored chunks for it were removed.
    """

    doc_id: str
    title: str
    chunk_count: int
    embedded: bool


class FederalRegisterDoc(BaseModel):
    document_number: str
    title: str | None
    abstract: str | None
    type: str | None
    publication_date: str | None
    agencies: str | None
    action: str | None
    effective_date: str | None
    html_url: str | None
    pdf_url: str | None


class GithubReadme(BaseModel):
    """One row from `readmes` with `status = 'fetched'`. Raw README body at /content."""

    repo: str
    owner: str | None
    name: str | None
    source_list: str | None
    readme_chars: int | None


class SecEdgarFiling(BaseModel):
    """One fetched SEC EDGAR filing. Extracted body text served at /content."""

    accession_number: str
    company_name: str | None
    cik: str | None
    form_type: str | None
    date_filed: str | None
    filing_url: str | None
    body_chars: int | None


class DownloadResult(BaseModel):
    """Result of an on-demand filing body download (`POST .../{accession}/download`).

    `status` is the row's new status: 'fetched' when text was stored, 'missing'
    when the submission held no extractable body, 'error' when it couldn't be
    fetched. `body_chars` is the stored body length (0 unless status='fetched').
    """

    accession_number: str
    status: str
    body_chars: int


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


class WBIndicator(BaseModel):
    """One World Bank indicator with its topic memberships."""

    id: str
    name: str
    unit: str | None
    source_note: str | None
    source_org: str | None
    topics: list[str]


class WBObservation(BaseModel):
    """One observed value for an indicator in a country/year."""

    country_id: str
    country_name: str | None
    year: int
    value: float


class WBCountry(BaseModel):
    """One economy (country or regional/income aggregate) from the World Bank."""

    id: str
    name: str
    region: str | None
    income_level: str | None


class WBDataPoint(BaseModel):
    """One indicator observation returned by the country data endpoint."""

    indicator_id: str
    indicator_name: str
    year: int
    value: float


class GeonamesFeatureClass(BaseModel):
    """One row of the GeoNames feature-class lookup (9 classes total)."""

    feature_class: str
    name: str
    description: str | None
    count: int | None


class GeonamesFeatureCode(BaseModel):
    """One row of the GeoNames feature-code lookup (~680 codes total)."""

    feature_class: str
    feature_code: str
    description: str | None


class Bill(BaseModel):
    """One row from `billstatus.bills`. Summary body served at /content."""

    bill_id: str
    congress: int | None
    bill_type: str | None
    bill_number: str | None
    title: str | None
    sponsor: str | None
    introduced_date: str | None
    latest_action: str | None
    policy_area: str | None
    subjects: list[str]
    summary_chars: int | None


class BillDetail(Bill):
    """Bill with full summary text included (detail endpoint only)."""

    summary: str | None


class GeonamesPlace(BaseModel):
    """One row from `geonames.places` — a single named geographic feature."""

    geonameid: int
    name: str | None
    latitude: float | None
    longitude: float | None
    feature_class: str | None
    feature_code: str | None
    feature_description: str | None
    country_code: str | None
    country_name: str | None
    population: int | None
    elevation: int | None
    timezone: str | None
    sentence: str | None
