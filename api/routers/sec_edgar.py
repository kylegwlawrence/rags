import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_fts_errors
from api.models import Page, SecEdgarFiling

router = APIRouter(prefix="/sec_edgar", tags=["sec_edgar"])


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


def _lookup(conn: sqlite3.Connection, accession_number: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT accession_number, company_name, cik, form_type, date_filed, "
        "       filing_url, body, length(body) AS body_chars "
        "FROM filings WHERE accession_number = ? AND status = 'fetched'",
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
    sort: str | None = Query(
        None,
        description="Sort order: 'newest' (default), 'oldest', or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> Page[SecEdgarFiling]:
    """List fetched SEC EDGAR filings with optional full-text, company, CIK, and year filters."""
    from_clause = "filings"
    clauses: list[str] = ["filings.status = 'fetched'"]
    params: list = []

    if q is not None:
        from_clause = "filings JOIN filings_fts ON filings_fts.rowid = filings.rowid"
        clauses.append("filings_fts MATCH ?")
        params.append(q)
    if company is not None:
        clauses.append("filings.company_name LIKE ?")
        params.append(f"%{company}%")
    if cik is not None:
        clauses.append("filings.cik = ?")
        params.append(cik)
    if year is not None:
        clauses.append("strftime('%Y', filings.date_filed) = ?")
        params.append(str(year))

    where = "WHERE " + " AND ".join(clauses)

    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")
    if sort == "oldest":
        order = "filings.date_filed ASC, filings.accession_number ASC"
    elif sort == "relevance":
        order = "bm25(filings_fts) ASC"
    else:
        order = "filings.date_filed DESC, filings.accession_number DESC"

    with translate_fts_errors(
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
    """Return the extracted body text for one filing as text/plain."""
    row = _lookup(conn, accession_number)
    if not row["body"]:
        raise HTTPException(status_code=404, detail="filing has no text content")
    return Response(content=row["body"], media_type="text/plain; charset=utf-8")


@router.get("/filings/{accession_number}", response_model=SecEdgarFiling)
def get_filing(
    accession_number: str,
    conn: sqlite3.Connection = Depends(db.sec_edgar),
) -> SecEdgarFiling:
    """Return metadata for one SEC EDGAR filing by accession number."""
    return _row_to_filing(_lookup(conn, accession_number))


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
