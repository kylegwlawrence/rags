import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_fts_errors
from api.models import Page, WikihowArticle

router = APIRouter(prefix="/wikihow", tags=["wikihow"])


def _row_to_article(row: sqlite3.Row) -> WikihowArticle:
    """Map an `articles` row to its response model. Raw step `text` lives at /content."""
    return WikihowArticle(
        id=row["id"],
        title=row["title"],
        section_label=row["section_label"],
        headline=row["headline"],
        text_chars=row["text_chars"],
    )


def _lookup(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row:
    """Fetch an `articles` row by id or raise 404."""
    row = conn.execute(
        "SELECT id, title, section_label, headline, text, "
        "       length(text) AS text_chars "
        "FROM articles WHERE id = ?",
        [article_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {article_id} not found")
    return row


@router.get("/articles", response_model=Page[WikihowArticle])
def list_articles(
    title: str | None = Query(
        None,
        description="Substring match on the guide title (case-insensitive via LIKE).",
    ),
    section_label: str | None = Query(
        None,
        description="Exact match on the step's section label (e.g. 'Using Home Remedies').",
    ),
    q: str | None = Query(
        None,
        description=(
            "FTS5 full-text search over title + headline + text. Accepts FTS5 "
            "syntax: bare words AND together, `\"phrase\"` for phrases, `term*` "
            "for prefix match, `a OR b`, `a NOT b`."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.wikihow),
) -> Page[WikihowArticle]:
    """List wikiHow step rows with title / section_label / FTS filters.

    Rows are per-step (the table's shape); `/wikihow/chunks` reassembles whole
    guides for retrieval.
    """
    from_clause = "articles"
    clauses: list[str] = []
    params: list = []
    if q is not None:
        from_clause = "articles JOIN articles_fts ON articles_fts.rowid = articles.id"
        clauses.append("articles_fts MATCH ?")
        params.append(q)
    if section_label is not None:
        clauses.append("articles.section_label = ?")
        params.append(section_label)
    if title is not None:
        clauses.append("articles.title LIKE ?")
        params.append(f"%{title}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "bm25(articles_fts) ASC" if q is not None else "articles.id ASC"

    with translate_fts_errors("wikihow", "wikihow/wikihow_index_fts.py", "data/wikihow/wikihow.db"):
        total = conn.execute(
            f"SELECT COUNT(*) FROM {from_clause} {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT articles.id, articles.title, articles.section_label, "
            f"       articles.headline, length(articles.text) AS text_chars "
            f"FROM {from_clause} {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[WikihowArticle](
        items=[_row_to_article(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# Content route comes BEFORE the detail route — route matching is order-sensitive
# and both share the {article_id} prefix.
@router.get("/articles/{article_id}/content")
def get_article_content(
    article_id: int,
    conn: sqlite3.Connection = Depends(db.wikihow),
) -> Response:
    """Return the raw step `text` body for one row as text/plain."""
    row = _lookup(conn, article_id)
    if not row["text"]:
        raise HTTPException(status_code=404, detail="article has no body")
    return Response(content=row["text"], media_type="text/plain; charset=utf-8")


@router.get("/articles/{article_id}", response_model=WikihowArticle)
def get_article(
    article_id: int,
    conn: sqlite3.Connection = Depends(db.wikihow),
) -> WikihowArticle:
    """Return metadata for one step row by id."""
    return _row_to_article(_lookup(conn, article_id))


add_chunks_route(
    router,
    opener=db.wikihow_rag,
    source_name="wikihow",
    indexer_script="wikihow/wikihow_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.wikihow_rag,
    source_name="wikihow",
    indexer_script="wikihow/wikihow_index_rag.py",
)
