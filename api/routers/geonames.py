"""GeoNames places router.

Backed by `data/geonames/geonames.db` (`places` table + `places_fts` FTS5
index over name + country_name + feature_description). No RAG / chunks
endpoint: rows are one-line records, not documents to retrieve.

Feature-class and feature-code descriptions are served from two CSV lookups
shipped in `data/geonames/` so the UI can render multi-select dropdowns
without having to embed the lists in JavaScript:

  - feature_classes.csv: 9 rows (A,H,L,P,R,S,T,U,V) with name + description + count
  - feature_codes.csv:   ~680 rows pairing each (class,code) with a description

The CSVs are loaded once at first request and cached at module scope.
"""

import csv
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from api import db
from api._fts import translate_table_errors
from api.models import (
    GeonamesFeatureClass,
    GeonamesFeatureCode,
    GeonamesPlace,
    Page,
)
from api.db import DATA_DIR

router = APIRouter(prefix="/geonames", tags=["geonames"])

_FEATURE_CLASSES_CSV = DATA_DIR / "geonames" / "feature_classes.csv"
_FEATURE_CODES_CSV = DATA_DIR / "geonames" / "feature_codes.csv"

_feature_classes_cache: list[GeonamesFeatureClass] | None = None
_feature_codes_cache: list[GeonamesFeatureCode] | None = None


def _load_feature_classes() -> list[GeonamesFeatureClass]:
    """Read feature_classes.csv once and cache the parsed rows.

    503 if the file is missing — same shape as a missing DB.
    """
    global _feature_classes_cache
    if _feature_classes_cache is not None:
        return _feature_classes_cache
    if not _FEATURE_CLASSES_CSV.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"feature_classes.csv not found at {_FEATURE_CLASSES_CSV}",
        )
    rows: list[GeonamesFeatureClass] = []
    with open(_FEATURE_CLASSES_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(GeonamesFeatureClass(
                feature_class=r["feature_class"],
                name=r["name"],
                description=r.get("description") or None,
                count=int(r["count"]) if r.get("count") else None,
            ))
    _feature_classes_cache = rows
    return rows


def _load_feature_codes() -> list[GeonamesFeatureCode]:
    """Read feature_codes.csv once and cache the parsed rows.

    503 if the file is missing.
    """
    global _feature_codes_cache
    if _feature_codes_cache is not None:
        return _feature_codes_cache
    if not _FEATURE_CODES_CSV.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"feature_codes.csv not found at {_FEATURE_CODES_CSV}",
        )
    rows: list[GeonamesFeatureCode] = []
    with open(_FEATURE_CODES_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(GeonamesFeatureCode(
                feature_class=r["feature_class"],
                feature_code=r["feature_code"],
                description=r.get("description") or None,
            ))
    _feature_codes_cache = rows
    return rows


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


@router.get("/feature_classes", response_model=list[GeonamesFeatureClass])
def list_feature_classes() -> list[GeonamesFeatureClass]:
    """Return all 9 GeoNames feature classes (A/H/L/P/R/S/T/U/V) with descriptions.

    Powers the 'Feature class' multi-select dropdown in the UI.
    """
    return _load_feature_classes()


@router.get("/feature_codes", response_model=list[GeonamesFeatureCode])
def list_feature_codes(
    feature_class: list[str] = Query(
        default=[],
        description=(
            "Optional class filter. Repeat the param to select multiple "
            "classes (`?feature_class=A&feature_class=P`); when empty, "
            "returns codes from every class."
        ),
    ),
) -> list[GeonamesFeatureCode]:
    """Return feature codes, optionally narrowed to the given class(es).

    Powers the 'Feature code' multi-select dropdown, whose options depend
    on the currently-selected feature classes.
    """
    rows = _load_feature_codes()
    if feature_class:
        wanted = {c.upper() for c in feature_class}
        rows = [r for r in rows if r.feature_class in wanted]
    return rows


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
    feature_class: list[str] = Query(
        default=[],
        description=(
            "Repeatable. Match places whose class is any of the given "
            "letters (A/H/L/P/R/S/T/U/V). Empty = no class filter."
        ),
    ),
    feature_code: list[str] = Query(
        default=[],
        description=(
            "Repeatable. Match places whose code is any of the given codes "
            "(e.g. 'PPL', 'MT'). Empty = no code filter."
        ),
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

    `feature_class` and `feature_code` are multi-value (OR within a list,
    AND across lists when both are set).
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
    if feature_class:
        placeholders = ",".join("?" * len(feature_class))
        clauses.append(f"places.feature_class IN ({placeholders})")
        params.extend(c.upper() for c in feature_class)
    if feature_code:
        placeholders = ",".join("?" * len(feature_code))
        clauses.append(f"places.feature_code IN ({placeholders})")
        params.extend(c.upper() for c in feature_code)
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
