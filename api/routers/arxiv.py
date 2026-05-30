import sqlite3
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_table_errors
from api.models import EmbedResult, Page, Paper
from rag import Doc, content_hash
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.embed_one import embed_doc
from rag.html_to_markdown import html_to_markdown
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/arxiv", tags=["arxiv"])

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


def _lookup_meta(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    """Fetch a `papers` row's metadata (no body) by id or raise 404.

    Used by the detail endpoint, which only reports `has_html` from
    `download_status` — fetching `html_content` here would pull a multi-MB
    body off disk on every detail request just to throw it away.
    """
    row = conn.execute(
        f"SELECT {_META_COLS} FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    return row


def _lookup_with_body(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    """Fetch a `papers` row including `html_content` by id or raise 404.

    Used by the content endpoint where the body is the response payload.
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, html_content FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    return row


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
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]

    with translate_table_errors("arxiv", "arxiv_index_fts.py", "data/arxiv/arxiv.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT papers.id, papers.title, papers.abstract, "
            f"       papers.primary_category, papers.categories, "
            f"       papers.submitted_date, papers.updated_date, papers.doi, "
            f"       papers.journal_ref, papers.comments, papers.download_status "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    # Re-use translate_table_errors for the paper_authors / authors join: if a
    # legacy DB predates Phase-3 normalization, the tables don't exist and we
    # want a 503 with the right hint. sql_error_is_user_input=False because a
    # malformed-SQL error here is the codebase's bug, not the caller's.
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "data/arxiv/arxiv.db",
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
    row = _lookup_with_body(conn, paper_id)
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


@router.get("/papers/{paper_id:path}", response_model=Paper)
def get_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Paper:
    """Return one paper by its arxiv id.

    `{paper_id:path}` so old-style ids with embedded slashes (e.g.
    `cond-mat/0204015`) match cleanly.
    """
    row = _lookup_meta(conn, paper_id)
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "data/arxiv/arxiv.db",
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
