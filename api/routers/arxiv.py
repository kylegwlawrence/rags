import sqlite3
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import EmbedResult, Page, Paper
from rag import Doc, content_hash
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.embed_one import embed_doc
from rag.html_to_markdown import html_to_markdown
from rag.profiles import DEFAULT as _PROFILE

router = APIRouter(prefix="/arxiv", tags=["arxiv"])

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

# arxiv is sharded by parent category across data/arxiv/{parent}.db, so a list
# query fans out to every shard and the rows are re-merged here. Each shard
# query also selects its sort key as `_sortkey` so we can re-sort the combined
# rows. Maps sort -> (key expression, reverse-for-Python-sort).
#
# Date sorts merge on the exact value. `relevance` is special: bm25 scores come
# from each shard's own FTS index and are NOT comparable across shards (a term
# that is rare in a small shard scores "better" there than the same term in the
# big shard where it's common), so merging on raw bm25 lets a small shard hijack
# the ranking. Instead relevance merges on each row's RANK within its shard
# (best-of-each-shard first), tie-broken by raw bm25 — scale-independent.
_SORT_KEY = {
    "submitted_desc": ("submitted_date", True),
    "submitted_asc": ("submitted_date", False),
    "updated_desc": ("updated_date", True),
    "relevance": ("bm25(papers_fts)", False),
}


def _merge_sort_key(row: sqlite3.Row):
    """Date-sort key for a row by its `_sortkey` column.

    None sorts lowest as ``(0, "")`` vs ``(1, value)`` for present values. Paired
    with ``reverse`` from `_SORT_KEY`, this reproduces SQLite's NULL ordering
    (NULLs last under DESC, first under ASC). Within one sort every `_sortkey`
    is the same type (all dates), so the values never cross-compare.
    """
    value = row["_sortkey"]
    return (0, "") if value is None else (1, value)


def _row_to_paper(row: sqlite3.Row, authors: list[str]) -> Paper:
    """Map a `papers` row + its ordered author display_names to the response model.

    `authors` is fetched separately (single-query batch for `list_papers`,
    per-paper for `get_paper`) — the normalized `paper_authors` / `authors`
    tables replaced the legacy JSON column in Phase 3. `papers.categories`
    is a whitespace-separated token string from the OAI feed.
    """
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


def _find_shard(
    shards: dict[str, sqlite3.Connection],
    paper_id: str,
    *,
    with_body: bool = False,
) -> tuple[sqlite3.Connection | None, sqlite3.Row | None]:
    """Locate the shard holding `paper_id` and return ``(conn, row)``.

    A paper lives in exactly one shard (home = its primary-category parent), so
    we probe shards in turn and stop at the first hit, returning ``(None, None)``
    when no shard has it. `with_body` pulls `html_content` too (the content
    route needs the body; the detail route only reports `has_html`, so it skips
    the multi-MB column).
    """
    cols = f"{_META_COLS}, html_content" if with_body else _META_COLS
    for conn in shards.values():
        row = conn.execute(
            f"SELECT {cols} FROM papers WHERE id = ?", [paper_id]
        ).fetchone()
        if row is not None:
            return conn, row
    return None, None


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
    shards: dict[str, sqlite3.Connection] = Depends(db.arxiv_shards),
) -> Page[Paper]:
    """List papers with category / date / author / has_html / FTS filters.

    arxiv is sharded by parent category, so this runs the same filtered query
    against every present shard, merges the per-shard tops, re-sorts globally,
    and returns the requested page. `total` is the summed count across shards.
    """
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
    key_expr, reverse = _SORT_KEY[sort]
    select_cols = (
        "papers.id, papers.title, papers.abstract, "
        "papers.primary_category, papers.categories, "
        "papers.submitted_date, papers.updated_date, papers.doi, "
        "papers.journal_ref, papers.comments, papers.download_status"
    )

    # Fan out: from each shard take its own count and its top (offset+limit) rows
    # in the requested order. The global page is a subset of the union of those
    # per-shard tops, so re-sorting and slicing here yields the correct page.
    # Each row keeps its 0-based rank within its shard for the relevance merge.
    fetch_n = offset + limit
    total = 0
    collected: list[tuple[str, int, sqlite3.Row]] = []
    for parent, sconn in shards.items():
        with translate_table_errors(
            "arxiv", "arxiv_index_fts.py", f"data/arxiv/{parent}.db"
        ):
            total += sconn.execute(
                f"SELECT COUNT(*) FROM {from_clause} {where}", params
            ).fetchone()[0]
            srows = sconn.execute(
                f"SELECT {select_cols}, {key_expr} AS _sortkey "
                f"FROM {from_clause} {where} ORDER BY {order} LIMIT ?",
                [*params, fetch_n],
            ).fetchall()
        collected.extend((parent, rank, r) for rank, r in enumerate(srows))

    if sort == "relevance":
        # Rank-within-shard first (best of each shard interleaved), bm25 as the
        # tiebreak. Scale-independent, so no shard dominates on raw bm25.
        collected.sort(key=lambda t: (t[1], t[2]["_sortkey"]))
    else:
        collected.sort(key=lambda t: _merge_sort_key(t[2]), reverse=reverse)
    page = collected[offset : offset + limit]

    # Authors are joined within the shard each row came from, so group page rows
    # by shard and batch-fetch per shard. translate_table_errors here gives a 503
    # with the right hint if a shard predates Phase-3 author normalization;
    # sql_error_is_user_input=False because malformed SQL would be our bug.
    ids_by_shard: dict[str, list[str]] = {}
    for parent, _rank, r in page:
        ids_by_shard.setdefault(parent, []).append(r["id"])
    authors_by_paper: dict[str, list[str]] = {}
    for parent, ids in ids_by_shard.items():
        with translate_table_errors(
            "arxiv",
            "arxiv_normalize_authors.py",
            f"data/arxiv/{parent}.db",
            sql_error_is_user_input=False,
        ):
            authors_by_paper.update(_fetch_authors_many(shards[parent], ids))
    return Page[Paper](
        items=[
            _row_to_paper(r, authors_by_paper.get(r["id"], []))
            for _parent, _rank, r in page
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes BEFORE the detail route because both use `{paper_id:path}`,
# which is greedy and would otherwise consume `.../content` as part of paper_id.
@router.get("/papers/{paper_id:path}/content")
def get_paper_content(
    paper_id: str,
    shards: dict[str, sqlite3.Connection] = Depends(db.arxiv_shards),
) -> Response:
    """Return the downloaded HTML body for one paper as text/html.

    404s distinguish paper-missing from no-html-downloaded so the caller can tell
    why. Content lives in the DB column, not on disk — gutenberg's FileResponse
    pattern doesn't apply here.
    """
    _conn, row = _find_shard(shards, paper_id, with_body=True)
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    if row["html_content"] is None:
        raise HTTPException(status_code=404, detail="paper has no downloaded HTML")
    return Response(content=row["html_content"], media_type="text/html; charset=utf-8")


@router.post("/papers/{paper_id:path}/embed", response_model=EmbedResult)
def embed_paper(
    paper_id: str,
    shards: dict[str, sqlite3.Connection] = Depends(db.arxiv_shards),
) -> EmbedResult:
    """Embed one arxiv paper into arxiv_rag.db on demand (synchronous).

    Mirrors `arxiv_rag_extract.iter_docs`: renders downloaded HTML via
    `html_to_markdown` (chunked section-aware) or falls back to the cleaned
    abstract / title when no body is on disk. Replaces any chunks already
    stored for this paper, so it becomes searchable through `/arxiv/chunks`
    immediately — the RAG DB runs in WAL mode, so the cached read-only
    connection picks up the new rows without a uvicorn restart.

    Returns `embedded=false` when the paper yields no chunks (genuinely empty
    body and abstract). A 503 means Ollama was unreachable; any existing
    chunks are left untouched.
    """
    # Find the shard holding this paper, pulling the columns the Doc needs.
    row = None
    for sconn in shards.values():
        row = sconn.execute(
            "SELECT id, title, abstract, html_content, oai_datestamp, updated_date "
            "FROM papers WHERE id = ?",
            [paper_id],
        ).fetchone()
        if row is not None:
            break
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")

    title = normalize_whitespace(strip_html(row["title"] or ""))
    html_content = row["html_content"]
    text = html_to_markdown(html_content).strip() if html_content else ""
    if not text:
        abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
        text = abstract or title
    html_marker = content_hash(html_content)[:8] if html_content else "no-html"
    base_version = row["oai_datestamp"] or content_hash(
        title, row["abstract"] or "", row["updated_date"]
    )
    doc = Doc(
        doc_id=row["id"],
        title=title or row["id"],
        version=f"{base_version}-{html_marker}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )

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


@router.get("/papers/{paper_id:path}", response_model=Paper)
def get_paper(
    paper_id: str,
    shards: dict[str, sqlite3.Connection] = Depends(db.arxiv_shards),
) -> Paper:
    """Return one paper by its arxiv id.

    `{paper_id:path}` so old-style ids with embedded slashes (e.g.
    `cond-mat/0204015`) match cleanly.
    """
    conn, row = _find_shard(shards, paper_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"paper {paper_id!r} not found")
    with translate_table_errors(
        "arxiv",
        "arxiv_normalize_authors.py",
        "data/arxiv/*.db",
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
