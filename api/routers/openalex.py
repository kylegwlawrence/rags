import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api.models import Chunk, ChunksResponse, Page, Work
from rag import retriever
from rag.retriever import is_operational_error

router = APIRouter(prefix="/openalex", tags=["openalex"])

SHORT_ID_RE = re.compile(r"^W\d+$")
OPENALEX_PREFIX = "https://openalex.org/"

SORTS = {
    "cited_by_count_desc": "cited_by_count DESC",
    "year_desc": "year DESC",
    "year_asc": "year ASC",
    # Lower bm25 = better FTS match. Only valid when `q` is set.
    "relevance": "bm25(works_fts) ASC",
}
Sort = Literal["cited_by_count_desc", "year_desc", "year_asc", "relevance"]


def _row_to_work(row: sqlite3.Row) -> Work:
    """Map a `works` row to its response model, splitting authors and shortening the id."""
    full_id = row["id"]
    short = full_id.rsplit("/", 1)[-1] if full_id else full_id
    # Split on the same separator the downloader uses to join names, so the
    # `authors` array in the response matches the rows in `work_authors`.
    authors_raw = row["authors"] or ""
    authors = [a.strip() for a in authors_raw.split(", ") if a.strip()]
    return Work(
        id=short,
        openalex_url=full_id,
        title=row["title"],
        abstract=row["abstract"],
        year=row["year"],
        cited_by_count=row["cited_by_count"],
        doi=row["doi"],
        authors=authors,
        venue=row["venue"],
    )


@router.get("/works/{short_id}", response_model=Work)
def get_work(
    short_id: str,
    conn: sqlite3.Connection = Depends(db.openalex),
) -> Work:
    """Return one work by its OpenAlex short id (e.g. `W3038568908`)."""
    if not SHORT_ID_RE.match(short_id):
        raise HTTPException(status_code=400, detail="id must look like W123456")
    full = OPENALEX_PREFIX + short_id
    row = conn.execute(
        "SELECT id, title, abstract, year, cited_by_count, doi, authors, venue "
        "FROM works WHERE id = ?",
        [full],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"work {short_id!r} not found")
    return _row_to_work(row)


@router.get("/works", response_model=Page[Work])
def list_works(
    year: int | None = Query(None),
    cited_by_min: int | None = Query(None, ge=0),
    cited_by_max: int | None = Query(None, ge=0),
    venue: str | None = Query(None, description="Exact venue match"),
    author: str | None = Query(
        None,
        description="Substring match against any of the work's authors (normalized table)",
    ),
    q: str | None = Query(
        None,
        description=(
            "Full-text search on title + abstract. Accepts FTS5 syntax: "
            "bare words AND together, `\"phrase\"` for phrases, `term*` for "
            "prefix match, `a OR b`, `a NOT b`."
        ),
    ),
    sort: Sort | None = Query(
        None,
        description=(
            "Defaults to `relevance` when `q` is set, otherwise `cited_by_count_desc`. "
            "`relevance` requires `q`."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.openalex),
) -> Page[Work]:
    """List works with year / citation / venue / author / full-text filters."""
    if sort is None:
        sort = "relevance" if q is not None else "cited_by_count_desc"
    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")

    # The FROM clause grows a JOIN when full-text search is active.
    from_clause = "works"
    clauses: list[str] = []
    params: list = []
    if q is not None:
        from_clause = "works JOIN works_fts ON works_fts.rowid = works.rowid"
        clauses.append("works_fts MATCH ?")
        params.append(q)
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    if cited_by_min is not None:
        clauses.append("cited_by_count >= ?")
        params.append(cited_by_min)
    if cited_by_max is not None:
        clauses.append("cited_by_count <= ?")
        params.append(cited_by_max)
    if venue is not None:
        clauses.append("venue = ?")
        params.append(venue)
    if author is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM work_authors wa "
            "JOIN authors a ON a.id = wa.author_id "
            "WHERE wa.work_id = works.id AND a.display_name LIKE ?)"
        )
        params.append(f"%{author}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]

    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT works.id, works.title, works.abstract, works.year, "
            f"       works.cited_by_count, works.doi, works.authors, works.venue "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    except sqlite3.OperationalError as e:
        # Most often a malformed FTS5 query (`q="("`, unbalanced quotes, etc.).
        raise HTTPException(status_code=400, detail=f"bad query: {e}") from e

    return Page[Work](
        items=[_row_to_work(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/chunks", response_model=ChunksResponse)
def search_chunks(
    q: str = Query(..., description="Natural-language query. Empty → 400."),
    top_k: int = Query(20, ge=1, le=100),
    candidate_k: int = Query(50, ge=10, le=200),
    rag_conn: sqlite3.Connection = Depends(db.openalex_rag),
) -> ChunksResponse:
    """Hybrid (FTS5 + sqlite-vec) retrieval over openalex chunks (top-5k by citation).

    Returns RRF-merged hits. `used_dense=False` means Ollama was unreachable
    and only sparse FTS hits contributed; the body is still useful.

    Errors: 400 on empty `q`; 503 when `openalex_rag.db` or its `chunks_fts` /
    `chunks_vec` tables are missing (run `scripts/openalex_index_rag.py`).
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must not be empty")
    try:
        result = retriever.retrieve(q, rag_conn, top_k=top_k, candidate_k=candidate_k)
    except sqlite3.OperationalError as e:
        if is_operational_error(e):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"openalex RAG data not ready ({e}). "
                    "Run scripts/openalex_index_rag.py or restore data/openalex/openalex_rag.db."
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
