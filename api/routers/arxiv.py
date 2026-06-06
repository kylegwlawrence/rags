import csv
import os
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import ArxivDownloadResult, EmbedResult, Page, Paper
from rag import Doc, content_hash
from rag.arxiv_fetch import fetch_paper_html
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.embed_one import embed_doc
from rag.html_to_markdown import html_to_markdown
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/arxiv", tags=["arxiv"])

# Contact address advertised to arXiv when fetching a paper's HTML on demand.
# arXiv's polite-access policy wants a mailto: in the User-Agent, so this is
# required — resolved at request time (not import) so an unset env var doesn't
# break the rest of the router. Mirrors sec_edgar's `_require_sec_email`.
_ARXIV_EMAIL_ENV = "DATASETS_EMAIL"


def _require_arxiv_user_agent() -> str:
    email = os.environ.get(_ARXIV_EMAIL_ENV)
    if not email:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{_ARXIV_EMAIL_ENV} env var is not set; arXiv requires a "
                "contact mailto: in the User-Agent. Set it (e.g. via .env) and "
                "restart uvicorn."
            ),
        )
    return f"datasets/0.1 (mailto:{email})"

# Live-embed chunk settings match `scripts/arxiv/arxiv_index_rag.py` (both
# pull from `rag.profiles.DEFAULT` and use `chunk_markdown`), so a paper
# embedded via the button produces the same chunks as a batch indexer run.
# The Doc-building logic mirrors `arxiv_rag_extract.iter_docs` — keep the two
# in sync if either changes.

SORTS = {
    "submitted_desc": "submitted_date DESC",
    "submitted_asc": "submitted_date ASC",
    "updated_desc": "updated_date DESC",
    # Lower bm25 = better FTS match. Only valid when `q` is set.
    "relevance": "bm25(papers_fts) ASC",
}
Sort = Literal["submitted_desc", "submitted_asc", "updated_desc", "relevance"]


def _row_to_paper(row: sqlite3.Row, authors: list[str]) -> Paper:
    """Map a `papers` row + its ordered author display_names to the response model.

    `authors` is fetched separately (single-query batch for `list_papers`,
    per-paper for `get_paper`) — the normalized `paper_authors` / `authors`
    tables replaced the legacy JSON column in Phase 3. `papers.categories`
    is a whitespace-separated token string from the OAI feed.
    """
    categories_raw = row["categories"]
    return Paper(
        id=row["id"],
        title=row["title"],
        abstract=row["abstract"],
        authors=authors,
        primary_category=row["primary_category"],
        categories=categories_raw.split() if categories_raw else [],
        submitted_date=row["submitted_date"],
        updated_date=row["updated_date"],
        doi=row["doi"],
        journal_ref=row["journal_ref"],
        comments=row["comments"],
        has_html=(row["download_status"] == "downloaded"),
    )


_META_COLS = (
    "id, title, abstract, primary_category, categories, "
    "submitted_date, updated_date, doi, journal_ref, comments, "
    "download_status"
)


def _fetch_authors_one(conn: sqlite3.Connection, paper_id: str) -> list[str]:
    """Return the ordered list of author display_names for one paper."""
    rows = conn.execute(
        "SELECT a.display_name FROM paper_authors pa "
        "JOIN authors a ON a.id = pa.author_id "
        "WHERE pa.paper_id = ? ORDER BY pa.position",
        (paper_id,),
    ).fetchall()
    return [r["display_name"] for r in rows]


def _fetch_authors_many(
    conn: sqlite3.Connection, paper_ids: list[str]
) -> dict[str, list[str]]:
    """Batch lookup: return ``{paper_id: [display_name, ...]}`` ordered by position."""
    if not paper_ids:
        return {}
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT pa.paper_id, a.display_name "
        f"FROM paper_authors pa JOIN authors a ON a.id = pa.author_id "
        f"WHERE pa.paper_id IN ({placeholders}) "
        f"ORDER BY pa.paper_id, pa.position",
        paper_ids,
    ).fetchall()
    out: dict[str, list[str]] = {pid: [] for pid in paper_ids}
    for r in rows:
        out[r["paper_id"]].append(r["display_name"])
    return out


# Category taxonomy lives in the repo's data dir (not the /datasets monolith),
# a small static reference table: code, parent, description, legacy, paper_count.
_CATEGORIES_CSV = db.DATA_DIR / "arxiv" / "categories.csv"


@lru_cache(maxsize=1)
def _load_categories() -> dict[str, str]:
    """Read the arxiv category taxonomy as ``{code: description}``.

    Cached — the file is small, static reference data. Cells are stripped of
    the padding whitespace the hand-aligned CSV carries.
    """
    with _CATEGORIES_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = [c.strip() for c in next(reader)]
        code_i = header.index("code")
        desc_i = header.index("description")
        mapping: dict[str, str] = {}
        for row in reader:
            if len(row) <= max(code_i, desc_i):
                continue  # skip short/blank rows
            code = row[code_i].strip()
            if code:
                mapping[code] = row[desc_i].strip()
    return mapping


@router.get("/categories")
def list_categories() -> dict[str, str]:
    """Map every arxiv category code to its human description.

    e.g. ``{"astro-ph.CO": "Cosmology and Nongalactic Astrophysics", ...}``.
    The frontend fetches this once to label the Category / Categories fields in
    a paper's metadata pane. 503 if the reference CSV is missing.
    """
    try:
        return _load_categories()
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"arxiv categories.csv not found at {_CATEGORIES_CSV}; "
                "category descriptions are unavailable."
            ),
        )


@router.get("/papers", response_model=Page[Paper])
def list_papers(
    q: str | None = Query(
        None,
        description=(
            "Full-text search on title + abstract. Accepts FTS5 syntax: "
            "bare words AND together, `\"phrase\"` for phrases, `term*` for "
            "prefix match, `a OR b`, `a NOT b`."
        ),
    ),
    primary_category: str | None = Query(
        None, description="Exact match against papers.primary_category (e.g. 'cs.CL')"
    ),
    category: str | None = Query(
        None,
        description=(
            "Substring match against the whitespace-separated papers.categories "
            "string. Loose: 'cs.C' will match 'cs.CL'."
        ),
    ),
    submitted_year: int | None = Query(None, ge=1900, le=2100),
    submitted_from: str | None = Query(
        None, description="ISO date, inclusive lower bound on submitted_date"
    ),
    submitted_to: str | None = Query(
        None, description="ISO date, inclusive upper bound on submitted_date"
    ),
    author: str | None = Query(
        None,
        description=(
            "Substring match against any of the paper's authors via the "
            "normalized `paper_authors` / `authors` tables."
        ),
    ),
    has_html: bool | None = Query(
        None, description="true → only papers with downloaded HTML; false → only those without"
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only papers whose body "
            "has been chunked into arxiv_rag.db, false = only papers not "
            "yet embedded. Omit to list all (the default). Cross-references "
            "arxiv_rag.db's docs_meta."
        ),
    ),
    sort: Sort | None = Query(
        None,
        description=(
            "Defaults to `relevance` when `q` is set, otherwise `submitted_desc`. "
            "`relevance` requires `q`."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Page[Paper]:
    """List papers with category / date / author / has_html / FTS filters."""
    if sort is None:
        sort = "relevance" if q is not None else "submitted_desc"
    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")

    # The FROM clause grows a JOIN when full-text search is active.
    # All SELECTed columns are table-qualified below to stay unambiguous under the JOIN.
    from_clause = "papers"
    clauses: list[str] = []
    params: list = []
    if q is not None:
        from_clause = "papers JOIN papers_fts ON papers_fts.rowid = papers.rowid"
        clauses.append("papers_fts MATCH ?")
        params.append(q)
    if primary_category is not None:
        clauses.append("primary_category = ?")
        params.append(primary_category)
    if category is not None:
        clauses.append("categories LIKE ?")
        params.append(f"%{category}%")
    if submitted_year is not None:
        clauses.append("submitted_date LIKE ?")
        params.append(f"{submitted_year}-%")
    if submitted_from is not None:
        clauses.append("submitted_date >= ?")
        params.append(submitted_from)
    if submitted_to is not None:
        clauses.append("submitted_date <= ?")
        params.append(submitted_to)
    if author is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM paper_authors pa "
            "JOIN authors a ON a.id = pa.author_id "
            "WHERE pa.paper_id = papers.id AND a.display_name LIKE ?)"
        )
        params.append(f"%{author}%")
    if has_html is not None:
        # IS / IS NOT are SQLite's null-safe comparators. Bare `!= 'downloaded'`
        # would silently drop rows where download_status IS NULL.
        clauses.append(
            "download_status IS 'downloaded'" if has_html else "download_status IS NOT 'downloaded'"
        )
    if embedded is not None:
        # arxiv doc_ids match papers.id 1:1 (both arxiv paper-id strings).
        c, p, empty = embedded_clauses(
            db.arxiv_rag, embedded=embedded, column="papers.id",
        )
        if empty:
            return Page[Paper](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]
    select_cols = (
        "papers.id, papers.title, papers.abstract, "
        "papers.primary_category, papers.categories, "
        "papers.submitted_date, papers.updated_date, papers.doi, "
        "papers.journal_ref, papers.comments, papers.download_status"
    )

    with translate_table_errors("arxiv", "arxiv_index_fts.py", "arxiv.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {select_cols} FROM {from_clause} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    # translate_table_errors here gives a 503 with the right hint if the DB
    # predates Phase-3 author normalization; sql_error_is_user_input=False
    # because malformed SQL would be our bug.
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "arxiv.db",
        sql_error_is_user_input=False,
    ):
        authors_by_paper = _fetch_authors_many(conn, [r["id"] for r in rows])
    return Page[Paper](
        items=[_row_to_paper(r, authors_by_paper.get(r["id"], [])) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes BEFORE the detail route because both use `{paper_id:path}`,
# which is greedy and would otherwise consume `.../content` as part of paper_id.
@router.get("/papers/{paper_id:path}/content")
def get_paper_content(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Response:
    """Return the downloaded HTML body for one paper as text/html.

    404s distinguish paper-missing from no-html-downloaded so the caller can tell
    why. Content lives in the DB column, not on disk — gutenberg's FileResponse
    pattern doesn't apply here.
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, html_content FROM papers WHERE id = ?", [paper_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    if row["html_content"] is None:
        raise HTTPException(status_code=404, detail="paper has no downloaded HTML")
    return Response(content=row["html_content"], media_type="text/html; charset=utf-8")


@router.post("/papers/{paper_id:path}/embed", response_model=EmbedResult)
def embed_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> EmbedResult:
    """Embed one arxiv paper into arxiv_rag.db on demand (synchronous).

    Mirrors `arxiv_rag_extract.iter_docs`: renders downloaded HTML via
    `html_to_markdown` (chunked section-aware) or falls back to the cleaned
    abstract / title when no body is on disk. Replaces any chunks already
    stored for this paper, so it becomes searchable through `/arxiv/chunks`
    immediately — the RAG DB runs in WAL mode, so the cached read-only
    connection picks up the new rows without a uvicorn restart.

    Returns `embedded=false` when the paper yields no chunks (genuinely empty
    body and abstract). A 503 means Ollama was unreachable; any existing
    chunks are left untouched.
    """
    row = conn.execute(
        "SELECT id, title, abstract, html_content, oai_datestamp, updated_date "
        "FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")

    title = normalize_whitespace(strip_html(row["title"] or ""))
    html_content = row["html_content"]
    text = html_to_markdown(html_content).strip() if html_content else ""
    if not text:
        abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
        text = abstract or title
    html_marker = content_hash(html_content)[:8] if html_content else "no-html"
    base_version = row["oai_datestamp"] or content_hash(
        title, row["abstract"] or "", row["updated_date"]
    )
    doc = Doc(
        doc_id=row["id"],
        title=title or row["id"],
        version=f"{base_version}-{html_marker}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.ARXIV_RAG_DB)
    try:
        chunk_count = embed_doc(
            rag_conn,
            doc,
            chunk_fn=chunk_markdown,
            chunk_size=_PROFILE.chunk_size,
            overlap=_PROFILE.overlap,
            max_chunk_size=_PROFILE.max_chunk_size,
        )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"embedding service (Ollama) unavailable: {e}",
        ) from e
    finally:
        rag_conn.close()

    return EmbedResult(
        doc_id=doc.doc_id,
        title=doc.title,
        chunk_count=chunk_count,
        embedded=chunk_count > 0,
    )


@router.post("/papers/{paper_id:path}/download", response_model=ArxivDownloadResult)
def download_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> ArxivDownloadResult:
    """Fetch one paper's LaTeXML HTML from arXiv on demand and store it (synchronous).

    `arxiv_download.py` fetches HTML bodies in bulk; this does the same for a
    single paper from the detail view — `arxiv.org/html/{id}` via the shared
    `rag.arxiv_fetch.fetch_paper_html` — so the Content tab and `/content` start
    serving it. The body is written onto `papers.html_content` with
    `download_status='downloaded'`; a 404 (arXiv has no HTML version for the
    paper) records `download_status='no_html'` and returns that status with a 200
    so the UI can show a clear "no HTML available" note instead of an error.

    The write goes through a fresh read-write connection; the cached read-only
    connection picks up the committed single-row UPDATE on its next query, so no
    uvicorn restart is needed (same in-place pattern as the SEC download route).
    A paper already carrying HTML returns immediately without re-fetching. A
    persistent fetch failure (repeated 429 / 5xx) returns 502 and leaves the
    row's status unchanged so a later attempt can retry. Building the FTS / RAG
    indexes over the new body stays a separate batch step (`arxiv_index_fts.py` /
    `arxiv_index_rag.py`) or the live `/embed` route.
    """
    row = conn.execute(
        "SELECT id, html_content FROM papers WHERE id = ?", [paper_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    if row["html_content"]:
        # Idempotent: already downloaded, don't re-hit arXiv.
        return ArxivDownloadResult(
            paper_id=paper_id, status="downloaded", html_chars=len(row["html_content"])
        )

    try:
        body = fetch_paper_html(paper_id, user_agent=_require_arxiv_user_agent())
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"could not fetch paper HTML from arXiv: {e}",
        ) from e

    now_iso = datetime.now(timezone.utc).isoformat()
    status = "no_html" if body is None else "downloaded"

    rw = db.connect_rw(db.ARXIV_DB)
    try:
        if body is None:
            rw.execute(
                "UPDATE papers SET download_status = 'no_html', downloaded_at = ? "
                "WHERE id = ?",
                (now_iso, paper_id),
            )
        else:
            rw.execute(
                "UPDATE papers SET html_content = ?, download_status = 'downloaded', "
                "downloaded_at = ? WHERE id = ?",
                (body, now_iso, paper_id),
            )
        rw.commit()
    finally:
        rw.close()

    return ArxivDownloadResult(
        paper_id=paper_id,
        status=status,
        html_chars=len(body) if body is not None else 0,
    )


@router.get("/papers/{paper_id:path}", response_model=Paper)
def get_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Paper:
    """Return one paper by its arxiv id.

    `{paper_id:path}` so old-style ids with embedded slashes (e.g.
    `cond-mat/0204015`) match cleanly.
    """
    row = conn.execute(
        f"SELECT {_META_COLS} FROM papers WHERE id = ?", [paper_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "arxiv.db",
        sql_error_is_user_input=False,
    ):
        authors = _fetch_authors_one(conn, paper_id)
    return _row_to_paper(row, authors)


add_chunks_route(
    router,
    opener=db.arxiv_rag,
    source_name="arxiv",
    indexer_script="arxiv_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.arxiv_rag,
    source_name="arxiv",
    indexer_script="arxiv_index_rag.py",
)
