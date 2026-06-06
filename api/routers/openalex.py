import os
import re
import sqlite3
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, OpenAlexDownloadResult, Page, Work
from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.embed_one import embed_doc
from rag.openalex_fetch import (
    NoPdfAvailable,
    ensure_body_status_table,
    fetch_work_pdf,
    record_body_status,
)
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/openalex", tags=["openalex"])

# Where on-demand PDF downloads land — the same drop folder the bulk
# `openalex_fetch_bodies.py` writes to, feeding the `pdfs` ingest pipeline.
OPENALEX_BODIES_DIR = db.DATA_DIR / "openalex" / "bodies"

# Contact address advertised to publisher hosts when fetching a PDF on demand.
# Resolved at request time (not import) so an unset env var doesn't break the
# rest of the router. Mirrors arxiv's `_require_arxiv_user_agent`.
_EMAIL_ENV = "DATASETS_EMAIL"


def _require_user_agent() -> str:
    email = os.environ.get(_EMAIL_ENV)
    if not email:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{_EMAIL_ENV} env var is not set; a contact mailto: is required "
                "in the User-Agent for polite PDF fetches. Set it (e.g. via .env) "
                "and restart uvicorn."
            ),
        )
    return f"datasets/0.1 (mailto:{email})"

# Live-embed chunk settings come from `rag.profiles.DEFAULT` — the same
# profile `scripts/openalex/openalex_index_rag.py` uses, so a work embedded
# via the button chunks identically to a batch indexer pass. Doc-building
# logic mirrors `openalex_rag_extract.iter_docs` (title + abstract only —
# openalex.db has no full body).

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


def _row_to_work(row: sqlite3.Row, authors: list[str]) -> Work:
    """Map a `works` row + its ordered author display_names to the response model.

    `authors` comes from the normalized `work_authors` / `authors` tables — fetched
    in batch by `_fetch_authors_many` for list endpoints, per-row for the detail
    endpoint. The denormalized `works.authors` column still exists from the
    downloader but is no longer the source of truth here.
    """
    full_id = row["id"]
    short = full_id.rsplit("/", 1)[-1] if full_id else full_id
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


def _fetch_authors_one(conn: sqlite3.Connection, work_id: str) -> list[str]:
    """Return the ordered list of author display_names for one work."""
    rows = conn.execute(
        "SELECT a.display_name FROM work_authors wa "
        "JOIN authors a ON a.id = wa.author_id "
        "WHERE wa.work_id = ? ORDER BY wa.position",
        (work_id,),
    ).fetchall()
    return [r["display_name"] for r in rows]


def _fetch_authors_many(
    conn: sqlite3.Connection, work_ids: list[str]
) -> dict[str, list[str]]:
    """Batch lookup: ``{work_id: [display_name, ...]}`` ordered by position."""
    if not work_ids:
        return {}
    placeholders = ",".join("?" * len(work_ids))
    rows = conn.execute(
        f"SELECT wa.work_id, a.display_name "
        f"FROM work_authors wa JOIN authors a ON a.id = wa.author_id "
        f"WHERE wa.work_id IN ({placeholders}) "
        f"ORDER BY wa.work_id, wa.position",
        work_ids,
    ).fetchall()
    out: dict[str, list[str]] = {wid: [] for wid in work_ids}
    for r in rows:
        out[r["work_id"]].append(r["display_name"])
    return out


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
        "SELECT id, title, abstract, year, cited_by_count, doi, venue "
        "FROM works WHERE id = ?",
        [full],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"work {short_id!r} not found")
    with translate_table_errors(
        "openalex", "openalex_normalize_authors.py", "data/openalex/openalex.db"
    ):
        authors = _fetch_authors_one(conn, full)
    return _row_to_work(row, authors)


@router.get("/works", response_model=Page[Work])
def list_works(
    year: int | None = Query(None),
    cited_by_min: int | None = Query(None, ge=0),
    cited_by_max: int | None = Query(None, ge=0),
    venue: str | None = Query(None, description="Exact venue match"),
    domain: str | None = Query(
        None,
        description=(
            "Exact match against the work's primary-topic domain, the top of "
            "the OpenAlex topic hierarchy (one of: 'Physical Sciences', "
            "'Social Sciences', 'Health Sciences', 'Life Sciences')."
        ),
    ),
    field: str | None = Query(
        None,
        description=(
            "Exact match against the work's primary-topic field, e.g. "
            "'Computer Science', 'Physics and Astronomy' (the level below domain)."
        ),
    ),
    author: str | None = Query(
        None,
        description="Substring match against any of the work's authors (normalized table)",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only works whose title + "
            "abstract has been chunked into openalex_rag.db, false = only "
            "works not yet embedded. Omit to list all (the default)."
        ),
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
    """List works with year / citation / venue / domain / field / author / full-text filters."""
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
    if domain is not None:
        clauses.append("domain = ?")
        params.append(domain)
    if field is not None:
        clauses.append("field = ?")
        params.append(field)
    if author is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM work_authors wa "
            "JOIN authors a ON a.id = wa.author_id "
            "WHERE wa.work_id = works.id AND a.display_name LIKE ?)"
        )
        params.append(f"%{author}%")
    if embedded is not None:
        # docs_meta stores short ids (W123…) but works.id is the full URL
        # (https://openalex.org/W123…) — re-prefix when splicing.
        c, p, empty = embedded_clauses(
            db.openalex_rag,
            embedded=embedded,
            column="works.id",
            id_transform=lambda sid: OPENALEX_PREFIX + sid,
        )
        if empty:
            return Page[Work](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]

    with translate_table_errors(
        "openalex", "openalex_index_fts.py", "data/openalex/openalex.db"
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT works.id, works.title, works.abstract, works.year, "
            f"       works.cited_by_count, works.doi, works.venue "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    with translate_table_errors(
        "openalex", "openalex_normalize_authors.py", "data/openalex/openalex.db"
    ):
        authors_by_work = _fetch_authors_many(conn, [r["id"] for r in rows])
    return Page[Work](
        items=[_row_to_work(r, authors_by_work.get(r["id"], [])) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/works/{short_id}/embed", response_model=EmbedResult)
def embed_work(
    short_id: str,
    conn: sqlite3.Connection = Depends(db.openalex),
) -> EmbedResult:
    """Embed one OpenAlex work into openalex_rag.db on demand (synchronous).

    Embeds title + abstract (openalex.db has no body content). Replaces any
    chunks already stored for this work, so it becomes searchable through
    `/openalex/chunks` immediately — the RAG DB runs in WAL mode, so the
    cached read-only connection picks up the new rows without a uvicorn
    restart.

    Returns `embedded=false` when both title and abstract are empty after
    cleanup. A 503 means Ollama was unreachable; existing chunks are
    untouched.
    """
    if not SHORT_ID_RE.match(short_id):
        raise HTTPException(status_code=400, detail="id must look like W123456")
    full = OPENALEX_PREFIX + short_id
    row = conn.execute(
        "SELECT id, title, abstract FROM works WHERE id = ?",
        [full],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"work {short_id!r} not found")

    title = normalize_whitespace(strip_html(row["title"] or ""))
    abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
    if title and abstract:
        text = f"{title}\n\n{abstract}"
    else:
        text = title or abstract
    # Fallback to the W-id (not '<untitled>') so the embedder's format_document
    # header doesn't push NULL-title works toward each other in vector space
    # via a shared placeholder string.
    display_title = title or short_id
    doc = Doc(
        doc_id=short_id,
        title=display_title,
        version=f"{content_hash(title, abstract)}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.OPENALEX_RAG_DB)
    try:
        chunk_count = embed_doc(
            rag_conn,
            doc,
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


@router.post("/works/{short_id}/download", response_model=OpenAlexDownloadResult)
def download_work_pdf(
    short_id: str,
    conn: sqlite3.Connection = Depends(db.openalex),
) -> OpenAlexDownloadResult:
    """Fetch one work's open-access PDF to disk on demand (synchronous).

    `openalex_fetch_bodies.py` downloads PDFs in bulk; this does the same for a
    single work from the detail view — the work's `pdf_url` / `oa_url` via the
    shared `rag.openalex_fetch.fetch_work_pdf` — saving the PDF under
    `data/openalex/bodies/{short_id}.pdf` for the `pdfs` ingest pipeline.
    OpenAlex stores no body text, so unlike the arXiv / SEC download routes this
    writes nothing back into openalex.db's content; it only records the outcome
    in the shared `body_status` table (so a later bulk run skips it).

    Returns `status='fetched'` (PDF saved) or `status='no_pdf'` (no accessible
    open-access PDF — terminal, returned with 200 so the UI can show a clear
    note). A transient fetch failure (network / persistent 5xx) returns 502 and
    records an `error` row so a later attempt can retry. A work already fetched
    (PDF still on disk) returns immediately without re-downloading.

    The `body_status` write goes through a fresh read-write connection; the
    cached read-only connection sees the committed row on its next query (same
    in-place pattern as the arXiv / SEC download routes), so no restart needed.
    """
    if not SHORT_ID_RE.match(short_id):
        raise HTTPException(status_code=400, detail="id must look like W123456")
    full = OPENALEX_PREFIX + short_id
    row = conn.execute(
        "SELECT pdf_url, oa_url, is_oa FROM works WHERE id = ?", [full]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"work {short_id!r} not found")

    dest = OPENALEX_BODIES_DIR / f"{short_id}.pdf"
    rw = db.connect_rw(db.OPENALEX_DB)
    try:
        ensure_body_status_table(rw)
        prior = rw.execute(
            "SELECT status, bytes FROM body_status WHERE work_id = ?", [short_id]
        ).fetchone()
        if prior and prior["status"] == "fetched" and dest.exists():
            # Idempotent: already on disk, don't re-hit the publisher.
            return OpenAlexDownloadResult(
                short_id=short_id, status="fetched", file_bytes=prior["bytes"] or 0
            )

        try:
            nbytes, src = fetch_work_pdf(
                [row["pdf_url"], row["oa_url"]],
                dest,
                user_agent=_require_user_agent(),
            )
        except NoPdfAvailable as e:
            record_body_status(rw, short_id, "no_pdf", note=str(e))
            return OpenAlexDownloadResult(short_id=short_id, status="no_pdf", file_bytes=0)
        except httpx.HTTPError as e:
            record_body_status(rw, short_id, "error", note=str(e))
            raise HTTPException(
                status_code=502, detail=f"could not fetch PDF: {e}"
            ) from e

        record_body_status(
            rw, short_id, "fetched", pdf_path=f"{short_id}.pdf",
            nbytes=nbytes, source_url=src,
        )
        return OpenAlexDownloadResult(short_id=short_id, status="fetched", file_bytes=nbytes)
    finally:
        rw.close()


add_chunks_route(
    router,
    opener=db.openalex_rag,
    source_name="openalex",
    indexer_script="openalex_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.openalex_rag,
    source_name="openalex",
    indexer_script="openalex_index_rag.py",
)
