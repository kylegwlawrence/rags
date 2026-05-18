import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api.models import Chunk, ChunksResponse, Page, Paper
from rag import retriever

router = APIRouter(prefix="/arxiv", tags=["arxiv"])

SORTS = {
    "submitted_desc": "submitted_date DESC",
    "submitted_asc": "submitted_date ASC",
    "updated_desc": "updated_date DESC",
    # Lower bm25 = better FTS match. Only valid when `q` is set.
    "relevance": "bm25(papers_fts) ASC",
}
Sort = Literal["submitted_desc", "submitted_asc", "updated_desc", "relevance"]


def _row_to_paper(row: sqlite3.Row) -> Paper:
    """Map a `papers` row to its response model.

    `papers.authors` is a JSON array of `"forenames keyname"` strings;
    `papers.categories` is a whitespace-separated token string from the OAI
    feed. Both are parsed here.
    """
    return Paper(
        id=row["id"],
        title=row["title"],
        abstract=row["abstract"],
        authors=json.loads(row["authors"]),
        primary_category=row["primary_category"],
        categories=row["categories"].split(),
        submitted_date=row["submitted_date"],
        updated_date=row["updated_date"],
        doi=row["doi"],
        journal_ref=row["journal_ref"],
        comments=row["comments"],
        has_html=(row["download_status"] == "downloaded"),
    )


def _lookup(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row:
    """Fetch a `papers` row by id or raise 404.

    Mirrors the gutenberg `_lookup` helper — both detail and content endpoints
    need the same fetch-or-404 step, so it lives in one place.
    """
    row = conn.execute(
        "SELECT id, title, abstract, authors, primary_category, categories, "
        "       submitted_date, updated_date, doi, journal_ref, comments, "
        "       download_status, html_content "
        "FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    return row


def _is_operational(err: sqlite3.OperationalError) -> bool:
    """True when the error means the DB / FTS index isn't ready, not bad SQL."""
    msg = str(err)
    return "no such table" in msg or "unable to open database file" in msg


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
            "Substring match against the raw JSON-encoded papers.authors text. "
            "Not normalized — matches across author boundaries are possible."
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
        clauses.append("authors LIKE ?")
        params.append(f"%{author}%")
    if has_html is not None:
        # IS / IS NOT are SQLite's null-safe comparators. Bare `!= 'downloaded'`
        # would silently drop rows where download_status IS NULL.
        clauses.append(
            "download_status IS 'downloaded'" if has_html else "download_status IS NOT 'downloaded'"
        )
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]

    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT papers.id, papers.title, papers.abstract, papers.authors, "
            f"       papers.primary_category, papers.categories, "
            f"       papers.submitted_date, papers.updated_date, papers.doi, "
            f"       papers.journal_ref, papers.comments, papers.download_status, "
            f"       papers.html_content "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    except sqlite3.OperationalError as e:
        if _is_operational(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"arxiv data not ready ({e}). "
                    "Run scripts/arxiv_index_fts.py or restore data/arxiv/arxiv.db."
                ),
            ) from e
        # Most often a malformed FTS5 query (`q="("`, unbalanced quotes, etc.).
        raise HTTPException(status_code=400, detail=f"bad query: {e}") from e

    return Page[Paper](
        items=[_row_to_paper(r) for r in rows],
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
    row = _lookup(conn, paper_id)
    if row["html_content"] is None:
        raise HTTPException(status_code=404, detail="paper has no downloaded HTML")
    return Response(content=row["html_content"], media_type="text/html; charset=utf-8")


@router.get("/papers/{paper_id:path}", response_model=Paper)
def get_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Paper:
    """Return one paper by its arxiv id.

    `{paper_id:path}` so old-style ids with embedded slashes (e.g.
    `cond-mat/0204015`) match cleanly.
    """
    return _row_to_paper(_lookup(conn, paper_id))


@router.get("/chunks", response_model=ChunksResponse)
def search_chunks(
    q: str = Query(..., description="Natural-language query. Empty → 400."),
    top_k: int = Query(20, ge=1, le=100),
    candidate_k: int = Query(50, ge=10, le=200),
    rag_conn: sqlite3.Connection = Depends(db.arxiv_rag),
) -> ChunksResponse:
    """Hybrid (FTS5 + sqlite-vec) retrieval over arxiv chunks.

    Returns RRF-merged hits. `used_dense=False` means Ollama was unreachable
    and only sparse FTS hits contributed; the body is still useful.

    Errors: 400 on empty `q`; 503 when `arxiv_rag.db` or its `chunks_fts` /
    `chunks_vec` tables are missing (run `scripts/arxiv_index_rag.py`).
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must not be empty")
    try:
        result = retriever.retrieve(q, rag_conn, top_k=top_k, candidate_k=candidate_k)
    except sqlite3.OperationalError as e:
        if _is_operational(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"arxiv RAG data not ready ({e}). "
                    "Run scripts/arxiv_index_rag.py or restore data/arxiv/arxiv_rag.db."
                ),
            ) from e
        raise HTTPException(status_code=400, detail=f"bad query: {e}") from e

    items = [
        Chunk(
            chunk_id=h.chunk_id,
            doc_id=h.doc_id,
            title=h.title,
            section=h.section,
            chunk_index=h.chunk_index,
            text=h.text,
            text_length=h.text_length,
            score=h.score,
        )
        for h in result.hits
    ]
    return ChunksResponse(
        items=items,
        used_dense=result.used_dense,
        top_k=top_k,
        candidate_k=candidate_k,
    )
