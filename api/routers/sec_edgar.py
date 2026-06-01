import os
import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import DownloadResult, EmbedResult, Page, SecEdgarFiling
from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, strip_html
from rag.embed_one import embed_doc
from rag.profiles import DEFAULT as _PROFILE
from rag.sec_filing import download_filing_content

router = APIRouter(prefix="/sec_edgar", tags=["sec_edgar"])

# Live-embed chunk settings come from `rag.profiles.DEFAULT` — the same
# profile `scripts/sec_edgar/sec_edgar_index_rag.py` uses (`chunk_doc`, flat
# prose; filing text has no reliable `##` heading structure). Doc-building
# mirrors `sec_edgar_rag_extract.iter_docs` — keep them in sync.

# Contact address advertised to SEC when downloading a filing on demand. SEC
# rejects requests without an identifying User-Agent, so this is required —
# resolved at request time rather than import so an unset env var doesn't
# break the rest of the router.
_SEC_EMAIL_ENV = "DATASETS_EMAIL"


def _require_sec_email() -> str:
    email = os.environ.get(_SEC_EMAIL_ENV)
    if not email:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{_SEC_EMAIL_ENV} env var is not set; cannot identify to SEC. "
                "Set it to your contact address (e.g. via .env) and restart uvicorn."
            ),
        )
    return email


def _row_to_filing(row: sqlite3.Row) -> SecEdgarFiling:
    return SecEdgarFiling(
        accession_number=row["accession_number"],
        company_name=row["company_name"],
        cik=row["cik"],
        form_type=row["form_type"],
        date_filed=row["date_filed"],
        filing_url=row["filing_url"],
        body_chars=row["body_chars"],
    )


_META_COLS = (
    "accession_number, company_name, cik, form_type, date_filed, "
    "filing_url, status, length(body) AS body_chars"
)


def _lookup_meta(conn: sqlite3.Connection, accession_number: str) -> sqlite3.Row:
    """Fetch a `filings` row's metadata (no body) by accession or raise 404.

    `length(body)` runs over the BLOB header alone, so it's cheap even when
    the body is hundreds of KB. No status filter: metadata-only filings
    (body not yet downloaded) are reachable so the detail view can show
    them and offer a download.
    """
    row = conn.execute(
        f"SELECT {_META_COLS} FROM filings WHERE accession_number = ?",
        [accession_number],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"filing {accession_number!r} not found")
    return row


def _lookup_with_body(conn: sqlite3.Connection, accession_number: str) -> sqlite3.Row:
    """Fetch a `filings` row including `body` text + `body_html` by accession or raise 404.

    `body` is the cleaned text the embed route chunks; `body_html` is the
    render-ready markup the Content view serves. Both ride along so the content
    and embed routes don't each need their own query.
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, body, body_html FROM filings WHERE accession_number = ?",
        [accession_number],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"filing {accession_number!r} not found")
    return row


@router.get("/filings", response_model=Page[SecEdgarFiling])
def list_filings(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over company name + filing body. Accepts FTS5 "
            "syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    company: str | None = Query(
        None,
        description="Substring match on the company name (case-insensitive via LIKE).",
    ),
    cik: str | None = Query(
        None,
        description="Exact match on the Central Index Key (company identifier).",
    ),
    year: int | None = Query(
        None,
        description="Filter to filings filed in this year.",
    ),
    downloaded: bool | None = Query(
        None,
        description=(
            "Filter by body-download state: true = only filings whose body has "
            "been downloaded, false = only those not yet downloaded. Omit to "
            "list all harvested filings (the default)."
        ),
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only filings chunked into "
            "sec_edgar_rag.db, false = only filings not yet embedded. "
            "Independent of `downloaded` — a filing must be downloaded "
            "before it can be embedded."
        ),
    ),
    sort: str | None = Query(
        None,
        description="Sort order: 'newest' (default), 'oldest', or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> Page[SecEdgarFiling]:
    """List SEC EDGAR filings with optional full-text, company, CIK, year, and download-state filters.

    Lists every harvested filing by default, including metadata-only rows whose
    body hasn't been downloaded yet (use the `downloaded` filter to narrow).
    `q` matches only downloaded filings — `filings_fts` indexes fetched bodies.
    """
    from_clause = "filings"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        from_clause = "filings JOIN filings_fts ON filings_fts.rowid = filings.rowid"
        clauses.append("filings_fts MATCH ?")
        params.append(q)
    # `IS` / `IS NOT` are null-safe here: a never-attempted row has status NULL,
    # which `status IS NOT 'fetched'` correctly treats as "not downloaded".
    if downloaded is True:
        clauses.append("filings.status IS 'fetched'")
    elif downloaded is False:
        clauses.append("filings.status IS NOT 'fetched'")
    if company is not None:
        clauses.append("filings.company_name LIKE ?")
        params.append(f"%{company}%")
    if cik is not None:
        clauses.append("filings.cik = ?")
        params.append(cik)
    if year is not None:
        clauses.append("strftime('%Y', filings.date_filed) = ?")
        params.append(str(year))
    if embedded is not None:
        # doc_id == accession_number, matches filings.accession_number 1:1.
        c, p, empty = embedded_clauses(
            db.sec_edgar_rag,
            embedded=embedded,
            column="filings.accession_number",
        )
        if empty:
            return Page[SecEdgarFiling](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")
    if sort == "oldest":
        order = "filings.date_filed ASC, filings.accession_number ASC"
    elif sort == "relevance":
        order = "bm25(filings_fts) ASC"
    else:
        order = "filings.date_filed DESC, filings.accession_number DESC"

    with translate_table_errors(
        "sec_edgar",
        "sec_edgar/sec_edgar_index_fts.py",
        "data/sec_edgar/sec_edgar.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT filings.accession_number, filings.company_name, filings.cik, "
            f"       filings.form_type, filings.date_filed, filings.filing_url, "
            f"       length(filings.body) AS body_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[SecEdgarFiling](
        items=[_row_to_filing(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route must come before the detail route — both share the same prefix.
@router.get("/filings/{accession_number}/content")
def get_filing_content(
    accession_number: str,
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> Response:
    """Return the rendered filing body as text/html.

    Serves the stored `body_html` (cleaned, render-ready markup) so the Content
    view can display the filing with its tables and headings intact. Rows
    fetched before `body_html` existed have only the cleaned text `body`; those
    are wrapped in `<pre>` so they still render. A row with neither 404s.
    """
    row = _lookup_with_body(conn, accession_number)
    if row["body_html"]:
        html = row["body_html"]
    elif row["body"]:
        # Legacy row fetched before body_html: show the cleaned text verbatim.
        from html import escape

        html = f"<pre>{escape(row['body'])}</pre>"
    else:
        raise HTTPException(status_code=404, detail="filing has no text content")
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.post("/filings/{accession_number}/download", response_model=DownloadResult)
def download_filing(
    accession_number: str,
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> DownloadResult:
    """Download one filing's body from SEC on demand and store it (synchronous).

    `sec_edgar_download.py` records only filing metadata + a `filing_url`. This
    fetches that submission, extracts the primary document, and writes both the
    cleaned text (`body`, for FTS / embedding) and the render-ready HTML
    (`body_html`, for the Content view) onto the row (status -> 'fetched'), so
    the Content tab and `/content` start serving it. It mirrors the standalone
    `sec_edgar_fetch_bodies.py --accession` path but runs in-process, sharing
    the same extractor (`rag.sec_filing`).

    The write goes through a fresh read-write connection; the cached read-only
    connection picks up the committed row on its next query — an in-place
    single-row UPDATE to the same file, so no uvicorn restart is needed (unlike
    a full re-index that replaces the file). A filing whose submission 404s or
    keeps failing returns 502; one with no extractable text returns 422. In
    both cases the row's `status` is recorded so the failure is visible.
    Building the FTS / RAG search indexes over the new body stays a separate
    batch step (`sec_edgar_index_fts.py` / `sec_edgar_index_rag.py`).
    """
    row = _lookup_meta(conn, accession_number)
    filing_url = row["filing_url"]
    if not filing_url:
        raise HTTPException(
            status_code=422, detail="filing has no filing_url to download from"
        )

    fetched = download_filing_content(
        filing_url, row["form_type"] or "", _require_sec_email()
    )
    if fetched is None:
        status, stored, stored_html = "error", None, None
    else:
        body, body_html = fetched
        if body.strip():
            status, stored, stored_html = "fetched", body, body_html
        else:
            status, stored, stored_html = "missing", None, None

    rw = db.connect_rw(db.SEC_EDGAR_DB)
    try:
        rw.execute(
            "UPDATE filings SET body = ?, body_html = ?, status = ? "
            "WHERE accession_number = ?",
            (stored, stored_html, status, accession_number),
        )
        rw.commit()
    finally:
        rw.close()

    if status == "error":
        raise HTTPException(
            status_code=502,
            detail="could not download filing from SEC (404 or repeated fetch failure)",
        )
    if status == "missing":
        raise HTTPException(
            status_code=422,
            detail="filing submission contained no extractable text",
        )

    return DownloadResult(
        accession_number=accession_number,
        status=status,
        body_chars=len(stored) if stored else 0,
    )


@router.post("/filings/{accession_number}/embed", response_model=EmbedResult)
def embed_filing(
    accession_number: str,
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> EmbedResult:
    """Embed one fetched SEC filing into sec_edgar_rag.db on demand (synchronous).

    Requires the body to have been downloaded first — `sec_edgar_download.py`
    records only metadata + `filing_url`, and the bytes are pulled in either
    by `sec_edgar_fetch_bodies.py` or the live "Download full filing" button
    (`POST .../download`). When body is missing this returns 409 with a hint
    so the UI can prompt the download step.

    Doc construction mirrors `sec_edgar_rag_extract.iter_docs`. Replaces any
    chunks already stored for the filing, becoming searchable through
    `/sec_edgar/chunks` immediately (the RAG DB runs in WAL mode, so the
    cached read-only connection sees the new rows without a uvicorn restart).
    A 503 means Ollama was unreachable; existing chunks are untouched.
    """
    row = _lookup_with_body(conn, accession_number)
    body = row["body"]
    if not body or not body.strip():
        raise HTTPException(
            status_code=409,
            detail=(
                "filing body has not been downloaded — POST "
                f"/sec_edgar/filings/{accession_number}/download first"
            ),
        )

    company = row["company_name"] or row["accession_number"]
    title = f"{company} {row['form_type']} {row['date_filed']}".strip()
    doc = Doc(
        doc_id=row["accession_number"],
        title=title,
        version=f"{content_hash(body)}-{CLEANER_VERSION}",
        text=strip_html(body),
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.SEC_EDGAR_RAG_DB)
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


@router.get("/filings/{accession_number}", response_model=SecEdgarFiling)
def get_filing(
    accession_number: str,
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> SecEdgarFiling:
    """Return metadata for one SEC EDGAR filing by accession number."""
    return _row_to_filing(_lookup_meta(conn, accession_number))


add_chunks_route(
    router,
    opener=db.sec_edgar_rag,
    source_name="sec_edgar",
    indexer_script="sec_edgar/sec_edgar_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.sec_edgar_rag,
    source_name="sec_edgar",
    indexer_script="sec_edgar/sec_edgar_index_rag.py",
)
