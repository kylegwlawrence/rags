import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api.models import Page, WBCountry, WBDataPoint, WBIndicator, WBObservation

router = APIRouter(prefix="/worldbank", tags=["worldbank"])


def _get_indicator_row(conn: sqlite3.Connection, indicator_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, unit, source_note, source_org FROM indicators WHERE id = ?",
        [indicator_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"indicator {indicator_id!r} not found")
    return row


def _topics_for(conn: sqlite3.Connection, indicator_id: str) -> list[str]:
    """Topic names for a single indicator. Use `_topics_for_many` in list paths."""
    return [
        r[0]
        for r in conn.execute(
            """SELECT t.name FROM topics t
               JOIN indicator_topics it ON it.topic_id = t.id
               WHERE it.indicator_id = ?
               ORDER BY t.id""",
            [indicator_id],
        ).fetchall()
    ]


def _topics_for_many(
    conn: sqlite3.Connection, indicator_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-fetch topic names keyed by indicator_id to avoid N+1 queries."""
    if not indicator_ids:
        return {}
    placeholders = ",".join("?" * len(indicator_ids))
    rows = conn.execute(
        f"""SELECT it.indicator_id, t.name FROM indicator_topics it
            JOIN topics t ON t.id = it.topic_id
            WHERE it.indicator_id IN ({placeholders})
            ORDER BY it.indicator_id, t.id""",
        indicator_ids,
    ).fetchall()
    out: dict[str, list[str]] = {iid: [] for iid in indicator_ids}
    for r in rows:
        out[r["indicator_id"]].append(r["name"])
    return out


def _row_to_indicator(row: sqlite3.Row, topics: list[str]) -> WBIndicator:
    return WBIndicator(
        id=row["id"],
        name=row["name"],
        unit=row["unit"],
        source_note=row["source_note"],
        source_org=row["source_org"],
        topics=topics,
    )


@router.get("/indicators", response_model=Page[WBIndicator])
def list_indicators(
    q: str | None = Query(
        None,
        description="Keyword search on indicator name (case-insensitive substring match).",
    ),
    topic: int | None = Query(None, description="Filter by topic ID (1–21)."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBIndicator]:
    """List indicators, optionally filtered by topic ID and/or name keyword."""
    clauses: list[str] = []
    params: list = []

    if q is not None:
        clauses.append("i.name LIKE ?")
        params.append(f"%{q}%")
    if topic is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM indicator_topics it"
            " WHERE it.indicator_id = i.id AND it.topic_id = ?)"
        )
        params.append(topic)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM indicators i {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, unit, source_note, source_org"
        f" FROM indicators i {where} ORDER BY i.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    topics_by_id = _topics_for_many(conn, [r["id"] for r in rows])
    return Page[WBIndicator](
        items=[_row_to_indicator(r, topics_by_id.get(r["id"], [])) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/indicators/{indicator_id}/values", response_model=Page[WBObservation])
def get_indicator_values(
    indicator_id: str,
    country: str | None = Query(
        None,
        description=(
            "Country code (ISO2 or ISO3) to restrict results to one economy. "
            "Either form works — the resolver looks up its alternate via "
            "`countries.iso2_code` so 'USA' and 'US' both match."
        ),
    ),
    year: int | None = Query(None, description="Filter to a specific year."),
    limit: int = Query(
        500,
        ge=1,
        le=2000,
        description=(
            "Max rows. Default 500 fits one year × ~300 economies (the worst "
            "case); cap raised to 2000 so multi-year unfiltered views aren't "
            "truncated mid-year."
        ),
    ),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBObservation]:
    """Return observations for one indicator across all economies and years.

    `observations.country_id` is a mix of ISO2 (real countries) and ISO3
    (aggregates like 'WLD', 'EUU') — what the WB API returns per row. The
    JOIN to `countries` therefore matches on either form so country names
    show up regardless.
    """
    _get_indicator_row(conn, indicator_id)

    clauses = ["o.indicator_id = ?"]
    params: list = [indicator_id]

    if country is not None:
        # Resolve the input to every form known for this country (id and
        # iso2_code) so the filter matches regardless of which form the
        # observation row happens to store.
        code = country.upper()
        forms = {code}
        row = conn.execute(
            "SELECT id, iso2_code FROM countries WHERE id = ? OR iso2_code = ? LIMIT 1",
            (code, code),
        ).fetchone()
        if row is not None:
            if row["id"]:
                forms.add(row["id"])
            if row["iso2_code"]:
                forms.add(row["iso2_code"])
        placeholders = ",".join(["?"] * len(forms))
        clauses.append(f"o.country_id IN ({placeholders})")
        params.extend(sorted(forms))
    if year is not None:
        clauses.append("o.year = ?")
        params.append(year)

    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"SELECT COUNT(*) FROM observations o {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT o.country_id,
                   COALESCE(c1.name, c2.name) AS country_name,
                   o.year, o.value
            FROM observations o
            LEFT JOIN countries c1 ON c1.id = o.country_id
            LEFT JOIN countries c2 ON c2.iso2_code = o.country_id
            {where}
            ORDER BY o.year DESC, o.country_id
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBObservation](
        items=[
            WBObservation(
                country_id=r["country_id"],
                country_name=r["country_name"],
                year=r["year"],
                value=r["value"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/indicators/{indicator_id}", response_model=WBIndicator)
def get_indicator(
    indicator_id: str,
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> WBIndicator:
    """Return metadata for one indicator by its World Bank ID (e.g. NY.GDP.MKTP.CD)."""
    row = _get_indicator_row(conn, indicator_id)
    return _row_to_indicator(row, _topics_for(conn, indicator_id))


@router.get("/countries", response_model=Page[WBCountry])
def list_countries(
    region: str | None = Query(
        None, description="Substring match on region name (case-insensitive)."
    ),
    income_level: str | None = Query(
        None, description="Substring match on income level (case-insensitive)."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBCountry]:
    """List all economies (countries and regional/income aggregates)."""
    clauses: list[str] = []
    params: list = []

    if region is not None:
        clauses.append("region LIKE ?")
        params.append(f"%{region}%")
    if income_level is not None:
        clauses.append("income_level LIKE ?")
        params.append(f"%{income_level}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM countries {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT id, name, region, income_level FROM countries {where}"
        f" ORDER BY name LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBCountry](
        items=[
            WBCountry(
                id=r["id"],
                name=r["name"],
                region=r["region"],
                income_level=r["income_level"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/countries/{country_id}/data", response_model=Page[WBDataPoint])
def get_country_data(
    country_id: str,
    topic: int | None = Query(None, description="Filter by topic ID (1–21)."),
    year: int | None = Query(None, description="Filter to a specific year."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.worldbank),
) -> Page[WBDataPoint]:
    """Return all indicator observations for one economy, optionally filtered by topic and year."""
    country_id = country_id.upper()
    if conn.execute(
        "SELECT 1 FROM countries WHERE id = ?", [country_id]
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail=f"country {country_id!r} not found")

    clauses = ["o.country_id = ?"]
    params: list = [country_id]

    if topic is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM indicator_topics it"
            " WHERE it.indicator_id = o.indicator_id AND it.topic_id = ?)"
        )
        params.append(topic)
    if year is not None:
        clauses.append("o.year = ?")
        params.append(year)

    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"SELECT COUNT(*) FROM observations o {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT o.indicator_id, ind.name AS indicator_name, o.year, o.value
            FROM observations o JOIN indicators ind ON ind.id = o.indicator_id
            {where}
            ORDER BY o.indicator_id, o.year DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return Page[WBDataPoint](
        items=[
            WBDataPoint(
                indicator_id=r["indicator_id"],
                indicator_name=r["indicator_name"],
                year=r["year"],
                value=r["value"],
            )
            for r in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
