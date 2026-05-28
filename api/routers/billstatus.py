import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._fts import translate_table_errors
from api.models import Bill, BillDetail, Page

router = APIRouter(prefix="/billstatus", tags=["billstatus"])


def _split_subjects(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s for s in (part.strip() for part in raw.split(";")) if s]


def _row_to_bill(row: sqlite3.Row) -> Bill:
    return Bill(
        bill_id=row["bill_id"],
        congress=row["congress"],
        bill_type=row["bill_type"],
        bill_number=row["bill_number"],
        title=row["title"],
        sponsor=row["sponsor"],
        introduced_date=row["introduced_date"],
        latest_action=row["latest_action"],
        policy_area=row["policy_area"],
        subjects=_split_subjects(row["subjects"]),
        summary_chars=row["summary_chars"],
    )


_META_COLS = (
    "bill_id, congress, bill_type, bill_number, title, sponsor, "
    "introduced_date, latest_action, policy_area, subjects, "
    "length(summary) AS summary_chars"
)


def _lookup_meta(conn: sqlite3.Connection, bill_id: str) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_META_COLS} FROM bills WHERE bill_id = ?",
        [bill_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"bill {bill_id!r} not found")
    return row


@router.get("/bills", response_model=Page[Bill])
def list_bills(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over title + summary + subjects. Accepts FTS5 "
            "syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    congress: int | None = Query(
        None,
        description="Filter to a specific Congress number (e.g. 118).",
    ),
    bill_type: str | None = Query(
        None,
        description=(
            "Filter by bill type (case-insensitive). Values: HR, S, HRES, SRES, "
            "HCONRES, SCONRES, HJRES, SJRES."
        ),
    ),
    sponsor: str | None = Query(
        None,
        description="Substring match on the sponsor's full name (case-insensitive).",
    ),
    policy_area: str | None = Query(
        None,
        description="Exact match on policy area (e.g. 'Health', 'Taxation').",
    ),
    subject: str | None = Query(
        None,
        description="Substring match against the bill's subjects list.",
    ),
    sort: str | None = Query(
        None,
        description="Sort order: 'newest' (default), 'oldest', or 'relevance' (requires q).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.billstatus),
) -> Page[Bill]:
    """List Congressional bills with optional full-text search and metadata filters."""
    from_clause = "bills"
    clauses: list[str] = []
    params: list = []

    if q is not None:
        from_clause = "bills JOIN bills_fts ON bills_fts.rowid = bills.rowid"
        clauses.append("bills_fts MATCH ?")
        params.append(q)
    if congress is not None:
        clauses.append("bills.congress = ?")
        params.append(congress)
    if bill_type is not None:
        clauses.append("bills.bill_type = ?")
        params.append(bill_type.upper())
    if sponsor is not None:
        clauses.append("bills.sponsor LIKE ?")
        params.append(f"%{sponsor}%")
    if policy_area is not None:
        clauses.append("bills.policy_area = ?")
        params.append(policy_area)
    if subject is not None:
        clauses.append("bills.subjects LIKE ?")
        params.append(f"%{subject}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if sort == "relevance" and q is None:
        raise HTTPException(status_code=400, detail="sort=relevance requires q")
    if sort == "oldest":
        order = "bills.introduced_date ASC, bills.bill_id ASC"
    elif sort == "relevance":
        order = "bm25(bills_fts) ASC"
    else:
        order = "bills.introduced_date DESC, bills.bill_id DESC"

    with translate_table_errors(
        "billstatus",
        "billstatus/billstatus_index_fts.py",
        "data/billstatus/billstatus.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT bills.bill_id, bills.congress, bills.bill_type, bills.bill_number, "
            f"       bills.title, bills.sponsor, bills.introduced_date, "
            f"       bills.latest_action, bills.policy_area, bills.subjects, "
            f"       length(bills.summary) AS summary_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[Bill](
        items=[_row_to_bill(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes before the detail route — both share the same prefix.
@router.get("/bills/{bill_id}/content")
def get_bill_content(
    bill_id: str,
    conn: sqlite3.Connection = Depends(db.billstatus),
) -> Response:
    """Return the bill's summary text as text/plain."""
    row = conn.execute(
        "SELECT summary FROM bills WHERE bill_id = ?", [bill_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"bill {bill_id!r} not found")
    if not row["summary"]:
        raise HTTPException(status_code=404, detail="bill has no summary text")
    return Response(content=row["summary"], media_type="text/plain; charset=utf-8")


@router.get("/bills/{bill_id}", response_model=BillDetail)
def get_bill(
    bill_id: str,
    conn: sqlite3.Connection = Depends(db.billstatus),
) -> BillDetail:
    """Return one bill with its full summary text."""
    row = conn.execute(
        f"SELECT {_META_COLS}, summary FROM bills WHERE bill_id = ?",
        [bill_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"bill {bill_id!r} not found")
    base = _row_to_bill(row)
    return BillDetail(**base.model_dump(), summary=row["summary"] or None)
