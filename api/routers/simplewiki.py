import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_fts_errors
from api.models import Article, Page

router = APIRouter(prefix="/simplewiki", tags=["simplewiki"])


def _row_to_article(row: sqlite3.Row) -> Article:
    """Map an `articles` row to its response model. text_content lives at /content."""
    return Article(
        page_id=row["page_id"],
        title=row["title"],
        namespace=row["namespace"],
        revision_id=row["revision_id"],
        timestamp=row["timestamp"],
        text_bytes=row["text_bytes"],
    )


def _lookup(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    """Fetch an `articles` row by page_id or raise 404."""
    row = conn.execute(
        "SELECT page_id, title, namespace, revision_id, timestamp, text_bytes, text_content "
        "FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return row


@router.get("/articles", response_model=Page[Article])
def list_articles(
    title: str | None = Query(
        None,
        description=(
            "Substring filter on title (case-insensitive via LIKE). Cheaper "
            "than `q` for prefix-style lookups but doesn't tokenise."
        ),
    ),
    q: str | None = Query(
        None,
        description=(
            "FTS5 trigram match on title. Finds substrings anywhere in the "
            "title (`q=ngin` matches 'Engine', 'Engineering', 'Origins'). "
            "FTS5 syntax supported: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    namespace: int = Query(
        0,
        description="MediaWiki namespace id (0 = main article namespace, the default).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Page[Article]:
    """List articles with namespace / title-substring / FTS5 filters."""
    clauses: list[str] = ["articles.namespace = ?"]
    params: list = [namespace]
    if q is not None:
        # IN-subquery (not JOIN) so the planner materialises FTS hits first
        # and probes articles by (namespace, page_id). A JOIN here drives from
        # `articles WHERE namespace=0` (~394k rows) and scans FTS per row,
        # turning a 2 ms query into a 150 s one. articles_fts has
        # content_rowid=page_id, so its rowid lines up with articles.page_id.
        clauses.append(
            "articles.page_id IN "
            "(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
        )
        params.append(q)
    if title is not None:
        clauses.append("articles.title LIKE ?")
        params.append(f"%{title}%")
    where = "WHERE " + " AND ".join(clauses)

    with translate_fts_errors("simplewiki", "simplewiki_parse.py", "data/simplewiki/simplewiki.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT articles.page_id, articles.title, articles.namespace, "
            f"       articles.revision_id, articles.timestamp, articles.text_bytes "
            f"FROM articles {where} "
            f"ORDER BY articles.page_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[Article](
        items=[_row_to_article(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes BEFORE the detail route — same reason as the arxiv
# router: route matching is order-sensitive and both share the {page_id}
# prefix.
@router.get("/articles/{page_id}/content")
def get_article_content(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Response:
    """Return the raw wikitext body for one article as text/plain.

    No server-side rendering — wikitext is the canonical representation. The
    /simplewiki/chunks endpoint already exposes the markdown-rendered body
    in chunked form for retrieval; downstream tools that want HTML can pipe
    this through their own renderer.
    """
    row = _lookup(conn, page_id)
    if not row["text_content"]:
        raise HTTPException(status_code=404, detail="article has no body")
    return Response(content=row["text_content"], media_type="text/plain; charset=utf-8")


@router.get("/articles/{page_id}", response_model=Article)
def get_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Article:
    """Return metadata for one article by page_id."""
    return _row_to_article(_lookup(conn, page_id))


add_chunks_route(
    router,
    opener=db.simplewiki_rag,
    source_name="simplewiki",
    indexer_script="simplewiki_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.simplewiki_rag,
    source_name="simplewiki",
    indexer_script="simplewiki_index_rag.py",
)
