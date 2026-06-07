import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import CategorySummary, EmbedResult, Page, WikinewsArticle
from rag import Doc
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION
from rag.embed_one import embed_doc
from rag.profiles import WIKINEWS as _PROFILE
from rag.wiki_render import convert_wikitext_to_html
from rag.wikitext import normalize_category, redirect_target, wikitext_to_markdown

router = APIRouter(prefix="/wikinews", tags=["wikinews"])

_META_COLS = "page_id, title, namespace, revision_id, timestamp, pub_date, text_bytes"


def _row_to_article(row: sqlite3.Row) -> WikinewsArticle:
    return WikinewsArticle(
        page_id=row["page_id"],
        title=row["title"],
        namespace=row["namespace"],
        revision_id=row["revision_id"],
        timestamp=row["timestamp"],
        pub_date=row["pub_date"],
        text_bytes=row["text_bytes"],
        redirect_to=None,
    )


def _lookup_meta(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_META_COLS}, substr(text_content, 1, 300) AS head "
        "FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return row


def _lookup_with_body(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_META_COLS}, text_content FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return row


_MAX_REDIRECT_HOPS = 10


def _find_by_title(conn: sqlite3.Connection, title: str) -> sqlite3.Row | None:
    sql = (
        "SELECT page_id, substr(text_content, 1, 300) AS head "
        "FROM articles WHERE namespace = 0 AND title = ? LIMIT 1"
    )
    row = conn.execute(sql, [title]).fetchone()
    if row is None and title:
        capitalised = title[0].upper() + title[1:]
        if capitalised != title:
            row = conn.execute(sql, [capitalised]).fetchone()
    return row


def _resolve_redirect(
    conn: sqlite3.Connection, start_text: str, start_page_id: int
) -> int | None:
    """Follow a redirect chain to the final target page_id, or return None."""
    target = redirect_target(start_text)
    if target is None:
        return None
    visited = {start_page_id}
    for _ in range(_MAX_REDIRECT_HOPS):
        row = _find_by_title(conn, target.replace("_", " ").strip())
        if row is None:
            return None
        page_id = row["page_id"]
        if page_id in visited:
            return None
        visited.add(page_id)
        next_target = redirect_target(row["head"])
        if next_target is None:
            return page_id
        target = next_target
    return None


_SELECT_COLS = (
    "articles.page_id, articles.title, articles.namespace, "
    "articles.revision_id, articles.timestamp, articles.pub_date, articles.text_bytes"
)


@router.get("/articles", response_model=Page[WikinewsArticle])
def list_articles(
    title: str | None = Query(
        None,
        description="Substring filter on title (case-insensitive LIKE).",
    ),
    q: str | None = Query(
        None,
        description=(
            "FTS5 trigram match on title and article body. Supports FTS5 syntax: "
            '``"phrase"``, ``term*``, ``a OR b``, ``a NOT b``.'
        ),
    ),
    namespace: int = Query(0, description="MediaWiki namespace id (0 = news articles)."),
    category: str | None = Query(
        None,
        description=(
            "Substring filter (case-insensitive) on category name. Backed by the "
            "page_categories table built by running: "
            "simplewiki_index_categories.py --db data/wikinews/wikinews.db."
        ),
    ),
    date_from: str | None = Query(
        None,
        description="Earliest publication date (ISO YYYY-MM-DD, inclusive).",
        alias="date_from",
    ),
    date_to: str | None = Query(
        None,
        description="Latest publication date (ISO YYYY-MM-DD, inclusive).",
        alias="date_to",
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only articles with chunks "
            "in wikinews_rag.db, false = only unembedded articles."
        ),
    ),
    sort: str = Query(
        "date",
        pattern="^(date|relevance)$",
        description=(
            "Sort order: ``date`` = newest-first by pub_date (default); "
            "``relevance`` = FTS rank (requires ``q``)."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> Page[WikinewsArticle]:
    """List news articles with date / title / FTS / category filters."""
    # Non-FTS filters — apply to both query paths.
    extra_clauses: list[str] = ["articles.namespace = ?"]
    extra_params: list = [namespace]

    if title is not None:
        extra_clauses.append("articles.title LIKE ?")
        extra_params.append(f"%{title}%")
    if category is not None:
        extra_clauses.append(
            "articles.page_id IN "
            "(SELECT page_id FROM page_categories WHERE category LIKE ?)"
        )
        extra_params.append(f"%{normalize_category(category)}%")
    if date_from is not None:
        extra_clauses.append("articles.pub_date >= ?")
        extra_params.append(date_from)
    if date_to is not None:
        extra_clauses.append("articles.pub_date <= ?")
        extra_params.append(date_to)
    if embedded is not None:
        c, p, empty = embedded_clauses(
            db.wikinews_rag,
            embedded=embedded,
            column="articles.page_id",
            id_transform=int,
        )
        if empty:
            return Page[WikinewsArticle](items=[], total=0, limit=limit, offset=offset)
        extra_clauses.extend(c)
        extra_params.extend(p)

    use_relevance = sort == "relevance" and q is not None

    with translate_table_errors("wikinews", "wikinews_parse.py", "data/wikinews/wikinews.db"):
        if use_relevance:
            # JOIN path: lets us ORDER BY bm25(articles_fts).
            # articles_fts.rowid == articles.page_id (content_rowid=page_id).
            join_where = "WHERE articles_fts MATCH ? AND " + " AND ".join(extra_clauses)
            join_params = [q, *extra_params]
            total = conn.execute(
                f"SELECT COUNT(*) FROM articles "
                f"JOIN articles_fts ON articles_fts.rowid = articles.page_id "
                f"{join_where}",
                join_params,
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM articles "
                f"JOIN articles_fts ON articles_fts.rowid = articles.page_id "
                f"{join_where} "
                f"ORDER BY bm25(articles_fts) LIMIT ? OFFSET ?",
                [*join_params, limit, offset],
            ).fetchall()
        else:
            # IN-subquery path: planner materialises FTS hits first, then probes
            # articles by page_id. Much faster than JOIN for date-sorted results.
            clauses = list(extra_clauses)
            params = list(extra_params)
            if q is not None:
                clauses.append(
                    "articles.page_id IN "
                    "(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
                )
                params.append(q)
            where = "WHERE " + " AND ".join(clauses)
            total = conn.execute(
                f"SELECT COUNT(*) FROM articles {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT {_SELECT_COLS} FROM articles {where} "
                f"ORDER BY articles.pub_date DESC, articles.page_id DESC "
                f"LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

    return Page[WikinewsArticle](
        items=[_row_to_article(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/categories", response_model=Page[CategorySummary])
def list_categories(
    q: str | None = Query(None, description="Substring filter on category name."),
    sort: str = Query(
        "count",
        pattern="^(count|name)$",
        description="Order by article count (desc, default) or category name (asc).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> Page[CategorySummary]:
    """List distinct categories with article counts (backed by page_categories table)."""
    clauses: list[str] = []
    params: list = []
    if q is not None:
        clauses.append("category LIKE ?")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "ORDER BY n DESC, category ASC" if sort == "count" else "ORDER BY category ASC"

    with translate_table_errors(
        "wikinews", "simplewiki_index_categories.py --db data/wikinews/wikinews.db", "data/wikinews/wikinews.db"
    ):
        total = conn.execute(
            f"SELECT COUNT(DISTINCT category) FROM page_categories {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT category, COUNT(*) AS n FROM page_categories {where} "
            f"GROUP BY category {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[CategorySummary](
        items=[CategorySummary(category=r["category"], article_count=r["n"]) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/resolve", response_model=WikinewsArticle)
def resolve_title(
    title: str = Query(..., description="Exact article title to resolve to a page."),
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> WikinewsArticle:
    """Resolve a namespace-0 article title to its row (index-backed)."""
    sql = (
        f"SELECT {_META_COLS} FROM articles INDEXED BY idx_articles_title "
        "WHERE title = ? AND namespace = 0 LIMIT 1"
    )
    name = title.replace("_", " ").strip()
    row = conn.execute(sql, [name]).fetchone()
    if row is None and name:
        capitalised = name[0].upper() + name[1:]
        if capitalised != name:
            row = conn.execute(sql, [capitalised]).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no article titled {title!r}")
    return _row_to_article(row)


@router.get("/articles/{page_id}/content")
def get_article_content(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> Response:
    """Render one article's wikitext to HTML for the Content view."""
    row = _lookup_with_body(conn, page_id)
    if not row["text_content"]:
        raise HTTPException(status_code=404, detail="article has no body")
    html = convert_wikitext_to_html(row["text_content"])
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/articles/{page_id}", response_model=WikinewsArticle)
def get_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> WikinewsArticle:
    """Return metadata for one article; redirect_to is set for #REDIRECT stubs."""
    row = _lookup_meta(conn, page_id)
    article = _row_to_article(row)
    article.redirect_to = _resolve_redirect(conn, row["head"] or "", page_id)
    try:
        article.categories = [
            r["category"]
            for r in conn.execute(
                "SELECT category FROM page_categories WHERE page_id = ? ORDER BY category",
                [page_id],
            )
        ]
    except sqlite3.OperationalError:
        article.categories = []
    return article


@router.post("/articles/{page_id}/embed", response_model=EmbedResult)
def embed_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.wikinews),
) -> EmbedResult:
    """Embed one article into wikinews_rag.db on demand (synchronous).

    Redirects and empty bodies return embedded=false. Replaces existing chunks.
    503 if Ollama is unreachable.
    """
    row = _lookup_with_body(conn, page_id)
    markdown = wikitext_to_markdown(row["text_content"] or "")
    doc = Doc(
        doc_id=str(row["page_id"]),
        title=row["title"],
        version=f"{row['revision_id']}-{CLEANER_VERSION}",
        text=markdown,
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.WIKINEWS_RAG_DB)
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


add_chunks_route(
    router,
    opener=db.wikinews_rag,
    source_name="wikinews",
    indexer_script="wikinews_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.wikinews_rag,
    source_name="wikinews",
    indexer_script="wikinews_index_rag.py",
)
