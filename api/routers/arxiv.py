import csv
import os
import sqlite3
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import ArxivDownloadResult, EmbedResult, Page, Paper
from rag import Doc, content_hash
from rag.arxiv_fetch import (
    body_filename,
    extract_pdf_text,
    fetch_paper_html,
    fetch_paper_pdf,
)
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.embed_one import embed_doc
from rag.html_to_markdown import html_to_markdown
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/arxiv", tags=["arxiv"])

# Contact address advertised to arXiv when fetching a paper's HTML on demand.
# arXiv's polite-access policy wants a mailto: in the User-Agent, so this is
# required — resolved at request time (not import) so an unset env var doesn't
# break the rest of the router. Mirrors sec_edgar's `_require_sec_email`.
_ARXIV_EMAIL_ENV = "DATASETS_EMAIL"


def _require_arxiv_user_agent() -> str:
    email = os.environ.get(_ARXIV_EMAIL_ENV)
    if not email:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{_ARXIV_EMAIL_ENV} env var is not set; arXiv requires a "
                "contact mailto: in the User-Agent. Set it (e.g. via .env) and "
                "restart uvicorn."
            ),
        )
    return f"datasets/0.1 (mailto:{email})"

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
    """Map a `papers` row + ordered author display_names to the response model."""
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


# Category taxonomy lives in the repo's data dir (not the /datasets monolith),
# a small static reference table: code, parent, description, legacy, paper_count.
_CATEGORIES_CSV = db.DATA_DIR / "arxiv" / "categories.csv"

# Friendly names for the parent archives the CSV carries no description row for
# (cs, math, …). The archives that *do* have their own CSV row — astro-ph and
# cond-mat (legacy rows) plus the standalone codes like hep-th / gr-qc — are
# labelled from that description, so they're omitted here. Used to label the
# parent "Category" dropdown.
_ARCHIVE_NAMES = {
    "cs": "Computer Science",
    "econ": "Economics",
    "eess": "Electrical Engineering and Systems Science",
    "math": "Mathematics",
    "nlin": "Nonlinear Sciences",
    "physics": "Physics",
    "q-bio": "Quantitative Biology",
    "q-fin": "Quantitative Finance",
    "stat": "Statistics",
}


@cache
def _load_taxonomy() -> list[dict[str, str]]:
    """Read categories.csv into ``{code, parent, description, legacy}`` rows (cached)."""
    with _CATEGORIES_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = [c.strip() for c in next(reader)]
        idx = {n: header.index(n) for n in ("code", "parent", "description", "legacy")}
        rows: list[dict[str, str]] = []
        for row in reader:
            if len(row) <= max(idx.values()):
                continue  # skip short/blank rows
            code = row[idx["code"]].strip()
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "parent": row[idx["parent"]].strip(),
                    "description": row[idx["description"]].strip(),
                    "legacy": row[idx["legacy"]].strip(),
                }
            )
    return rows


@cache
def _load_categories() -> dict[str, str]:
    """The arxiv taxonomy as ``{code: description}`` (cached)."""
    return {r["code"]: r["description"] for r in _load_taxonomy()}


@cache
def _archive_parents() -> list[dict[str, str]]:
    """Top-level archives for the "Category" dropdown, sorted by code (cached)."""
    rows = _load_taxonomy()
    desc_by_code = {r["code"]: r["description"] for r in rows}
    parents = {r["parent"] for r in rows if r["parent"]}
    standalone = {r["code"] for r in rows if not r["parent"] and r["legacy"] == "false"}
    out: list[dict[str, str]] = []
    for code in sorted(parents | standalone):
        name = desc_by_code.get(code) or _ARCHIVE_NAMES.get(code) or code
        out.append({"code": code, "name": name})
    return out


def _subcategories(archive: str) -> list[dict[str, str]]:
    """Subcategory ``{code, description}`` rows under one parent archive.

    Sorted by code; empty list when ``archive`` is falsy.
    """
    if not archive:
        return []
    rows = [r for r in _load_taxonomy() if r["parent"] == archive]
    rows.sort(key=lambda r: r["code"])
    return [{"code": r["code"], "description": r["description"]} for r in rows]


@router.get("/categories")
def list_categories() -> dict[str, str]:
    """Return `{code: description}` for every arxiv category. 503 if the CSV is missing."""
    try:
        return _load_categories()
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"arxiv categories.csv not found at {_CATEGORIES_CSV}; "
                "category descriptions are unavailable."
            ),
        )


def _categories_missing() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            f"arxiv categories.csv not found at {_CATEGORIES_CSV}; "
            "category filters are unavailable."
        ),
    )


@router.get("/category-parents")
def list_category_parents() -> list[dict[str, str]]:
    """Top-level archive codes + names for the "Category" dropdown. 503 if CSV missing."""
    try:
        return _archive_parents()
    except FileNotFoundError:
        raise _categories_missing()


@router.get("/subcategories")
def list_subcategories(
    archive: str | None = Query(
        None,
        description="Parent archive code (e.g. 'cs'); returns its subcategories.",
    ),
) -> list[dict[str, str]]:
    """Subcategories under one archive, sorted by code. Empty when `archive` is unset."""
    try:
        return _subcategories(archive or "")
    except FileNotFoundError:
        raise _categories_missing()


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
    archive: str | None = Query(
        None,
        description=(
            "Parent-archive match: papers carrying any category in this archive "
            "— a token equal to it (a bare archive like 'gr-qc') or prefixed "
            "'<archive>.' (e.g. 'cs' → cs.AI, cs.LG). Token-precise, so 'cs' "
            "won't match 'physics'. Pairs with the Subcategory `category` filter."
        ),
    ),
    category: str | None = Query(
        None,
        description=(
            "Substring match against the whitespace-separated papers.categories "
            "string. Loose: 'cs.C' will match 'cs.CL'. Used for the Subcategory "
            "filter, which sends a full code (e.g. 'cs.AI')."
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
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only papers whose body "
            "has been chunked into arxiv_rag.db, false = only papers not "
            "yet embedded. Omit to list all (the default). Cross-references "
            "arxiv_rag.db's docs_meta."
        ),
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
    if archive is not None:
        # Token-precise archive match on the space-separated categories string:
        # an exact token (bare archive like 'gr-qc') or one prefixed
        # '<archive>.' (e.g. cs.AI). Padding both ends with spaces lets the
        # first/last token match too. Avoids the substring trap where '%cs%'
        # would also hit 'physics'. (Archive codes carry no LIKE wildcards.)
        clauses.append(
            "((' ' || categories || ' ') LIKE ? OR (' ' || categories || ' ') LIKE ?)"
        )
        params.append(f"% {archive} %")
        params.append(f"% {archive}.%")
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
    if embedded is not None:
        # arxiv doc_ids match papers.id 1:1 (both arxiv paper-id strings).
        c, p, empty = embedded_clauses(
            db.arxiv_rag, embedded=embedded, column="papers.id",
        )
        if empty:
            return Page[Paper](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS[sort]
    select_cols = (
        "papers.id, papers.title, papers.abstract, "
        "papers.primary_category, papers.categories, "
        "papers.submitted_date, papers.updated_date, papers.doi, "
        "papers.journal_ref, papers.comments, papers.download_status"
    )

    with translate_table_errors("arxiv", "arxiv_index_fts.py", "arxiv.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {select_cols} FROM {from_clause} {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    # translate_table_errors here gives a 503 with the right hint if the DB
    # predates Phase-3 author normalization; sql_error_is_user_input=False
    # because malformed SQL would be our bug.
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "arxiv.db",
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
    """Return the downloaded body for one paper.

    HTML papers render as text/html; papers that only have a PDF fallback
    return their extracted text as text/plain.
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, html_content, pdf_text FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    if row["html_content"] is not None:
        return Response(
            content=row["html_content"], media_type="text/html; charset=utf-8"
        )
    if row["pdf_text"]:
        return Response(
            content=row["pdf_text"], media_type="text/plain; charset=utf-8"
        )
    raise HTTPException(status_code=404, detail="paper has no downloaded body")


def _build_doc(row: sqlite3.Row) -> Doc:
    """Build the RAG Doc for one paper row.

    Body precedence: rendered HTML, else PDF-fallback text, else the abstract
    (title only as a last resort, since it's already in the embed prefix). The
    version carries an 8-char hash of whichever body exists so a paper re-embeds
    when it later gains an HTML or PDF body. Mirrors
    ``scripts/arxiv/arxiv_rag_extract.iter_docs`` — keep the two in sync.
    """
    title = normalize_whitespace(strip_html(row["title"] or ""))
    html_content = row["html_content"]
    pdf_text = row["pdf_text"]
    if html_content:
        text = html_to_markdown(html_content).strip()
    elif pdf_text:
        text = pdf_text.strip()
    else:
        text = ""
    if not text:
        abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
        text = abstract or title
    body = html_content or pdf_text
    body_marker = content_hash(body)[:8] if body else "no-body"
    base_version = row["oai_datestamp"] or content_hash(
        title, row["abstract"] or "", row["updated_date"]
    )
    return Doc(
        doc_id=row["id"],
        title=title or row["id"],
        version=f"{base_version}-{body_marker}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )


@router.post("/papers/{paper_id:path}/embed", response_model=EmbedResult)
def embed_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> EmbedResult:
    """Embed one arxiv paper into arxiv_rag.db on demand (synchronous).

    Renders downloaded HTML, else PDF-fallback text, else abstract/title.
    Replaces any existing chunks; searchable immediately. 503 if Ollama is
    unreachable.
    """
    row = conn.execute(
        "SELECT id, title, abstract, html_content, pdf_text, oai_datestamp, "
        "updated_date FROM papers WHERE id = ?",
        [paper_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")

    doc = _build_doc(row)

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


@router.post("/papers/{paper_id:path}/download", response_model=ArxivDownloadResult)
def download_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> ArxivDownloadResult:
    """Fetch one paper's body on demand and write it to the papers row (synchronous).

    Tries the LaTeXML HTML first; on a 404 falls back to the PDF, saving the
    raw file beside arxiv.db (``<db parent>/bodies/``) and its extracted text to
    ``papers.pdf_text``. Idempotent; ``status='no_body'`` (200) if arXiv has
    neither; 502 on fetch failure. ``html_chars`` is the stored body's length.
    """
    row = conn.execute(
        "SELECT id, html_content, pdf_text FROM papers WHERE id = ?", [paper_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    if row["html_content"]:
        # Idempotent: already downloaded, don't re-hit arXiv.
        return ArxivDownloadResult(
            paper_id=paper_id, status="downloaded", html_chars=len(row["html_content"])
        )
    if row["pdf_text"]:
        return ArxivDownloadResult(
            paper_id=paper_id, status="downloaded_pdf", html_chars=len(row["pdf_text"])
        )

    user_agent = _require_arxiv_user_agent()
    try:
        body = fetch_paper_html(paper_id, user_agent=user_agent)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"could not fetch paper HTML from arXiv: {e}",
        ) from e

    if body is not None:
        _write_arxiv_body(
            paper_id,
            "UPDATE papers SET html_content = ?, download_status = 'downloaded', "
            "downloaded_at = ? WHERE id = ?",
            (body,),
        )
        return ArxivDownloadResult(
            paper_id=paper_id, status="downloaded", html_chars=len(body)
        )

    # No HTML — fall back to the PDF.
    try:
        pdf_bytes = fetch_paper_pdf(paper_id, user_agent=user_agent)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"could not fetch paper PDF from arXiv: {e}",
        ) from e

    if pdf_bytes is None:
        _write_arxiv_body(
            paper_id,
            "UPDATE papers SET download_status = 'no_body', downloaded_at = ? "
            "WHERE id = ?",
            (),
        )
        return ArxivDownloadResult(paper_id=paper_id, status="no_body", html_chars=0)

    bodies_dir = Path(db.ARXIV_DB).parent / "bodies"
    bodies_dir.mkdir(parents=True, exist_ok=True)
    (bodies_dir / body_filename(paper_id)).write_bytes(pdf_bytes)
    try:
        text = extract_pdf_text(pdf_bytes)
    except Exception:
        # Keep the saved PDF for debugging; store empty text so the row resolves.
        text = ""
    _write_arxiv_body(
        paper_id,
        "UPDATE papers SET pdf_text = ?, download_status = 'downloaded_pdf', "
        "downloaded_at = ? WHERE id = ?",
        (text,),
    )
    return ArxivDownloadResult(
        paper_id=paper_id, status="downloaded_pdf", html_chars=len(text)
    )


def _write_arxiv_body(
    paper_id: str, sql: str, leading_params: tuple
) -> None:
    """Run one in-place UPDATE on arxiv.db via a fresh RW connection.

    ``sql`` must end with ``downloaded_at = ? WHERE id = ?``; this stamps the
    timestamp and id after any ``leading_params`` (the body value, if present).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    rw = db.connect_rw(db.ARXIV_DB)
    try:
        rw.execute(sql, (*leading_params, now_iso, paper_id))
        rw.commit()
    finally:
        rw.close()


@router.get("/papers/{paper_id:path}", response_model=Paper)
def get_paper(
    paper_id: str,
    conn: sqlite3.Connection = Depends(db.arxiv),
) -> Paper:
    """Return one paper by its arxiv id (`{paper_id:path}` handles slash-containing old-style ids)."""
    row = conn.execute(
        f"SELECT {_META_COLS} FROM papers WHERE id = ?", [paper_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "arxiv.db",
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
