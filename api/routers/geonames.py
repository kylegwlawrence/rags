"""GeoNames places router.

Backed by `data/geonames/geonames.db` (`places` table + `places_fts` FTS5
index over name + country_name + feature_description). No RAG / chunks
endpoint: rows are one-line records, not documents to retrieve.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api._fts import translate_table_errors
from api.models import GeonamesPlace, Page

router = APIRouter(prefix="/geonames", tags=["geonames"])


def _row_to_place(row: sqlite3.Row) -> GeonamesPlace:
    """Map a `places` row to its response model."""
    return GeonamesPlace(
        geonameid=row["geonameid"],
        name=row["name"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        feature_class=row["feature_class"],
        feature_code=row["feature_code"],
        feature_description=row["feature_description"],
        country_code=row["country_code"],
        country_name=row["country_name"],
        population=row["population"],
        elevation=row["elevation"],
        timezone=row["timezone"],
        sentence=row["sentence"],
    )


@router.get("/places", response_model=Page[GeonamesPlace])
def list_places(
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over name + country_name + "
            "feature_description. Accepts FTS5 syntax: bare words AND "
            "together, `\"phrase\"` for phrases, `term*` for prefix match, "
            "`a OR b`, `a NOT b`."
        ),
    ),
    country_code: str | None = Query(
        None,
        description="ISO-2 country code, exact match (e.g. 'US', 'FR').",
    ),
    feature_class: str | None = Query(
        None,
        description=(
            "Feature class, exact match. One of: A (admin), H (water), L "
            "(area), P (populated place), R (road), S (spot/building), T "
            "(terrain), U (undersea), V (vegetation)."
        ),
    ),
    feature_code: str | None = Query(
        None,
        description="GeoNames feature code, exact match (e.g. 'PPL', 'MT', 'STM').",
    ),
    min_population: int | None = Query(
        None,
        ge=0,
        description="Lower bound on population. 0 matches places with unknown/zero population.",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.geonames),
) -> Page[GeonamesPlace]:
    """List geographic places with FTS / country / feature / population filters.

    Default sort is population DESC so notable places surface first; when `q`
    is given, sort by FTS relevance (bm25) instead. With 13M+ rows, always
    include at least one filter — open queries scan the full table.
    """
    from_clause = "places"
    clauses: list[str] = []
    params: list = []
    if q is not None:
        from_clause = "places JOIN places_fts ON places_fts.rowid = places.geonameid"
        clauses.append("places_fts MATCH ?")
        params.append(q)
    if country_code is not None:
        clauses.append("places.country_code = ?")
        params.append(country_code.upper())
    if feature_class is not None:
        clauses.append("places.feature_class = ?")
        params.append(feature_class.upper())
    if feature_code is not None:
        clauses.append("places.feature_code = ?")
        params.append(feature_code.upper())
    if min_population is not None:
        clauses.append("places.population >= ?")
        params.append(min_population)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "bm25(places_fts) ASC" if q is not None else "places.population DESC"

    with translate_table_errors(
        "geonames",
        "geonames/geonames_index_fts.py",
        "data/geonames/geonames.db",
    ):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT places.* FROM {from_clause} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[GeonamesPlace](
        items=[_row_to_place(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/places/{geonameid}", response_model=GeonamesPlace)
def get_place(
    geonameid: int,
    conn: sqlite3.Connection = Depends(db.geonames),
) -> GeonamesPlace:
    """Return one place by GeoNames id."""
    row = conn.execute(
        "SELECT * FROM places WHERE geonameid = ?",
        [geonameid],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"place {geonameid} not found")
    return _row_to_place(row)
