"""Read-only FastAPI service that exposes an enwiki SQLite DB over HTTP.

Runs on a remote host (e.g. raspberrypi6) and is proxied by the datasets API's
`/enwiki/*` router. No auth — assumes the host is on a trusted network
(Tailscale ACLs). Pairs with `api/routers/enwiki.py` on the calling side.

Routes:
    GET /health                       Liveness + article-count summary
    GET /articles                     List with title-FTS / namespace / substring filters
    GET /articles/{page_id}           One article's metadata
    GET /articles/{page_id}/content   Raw wikitext body (text/plain)

Run:
    source ~/datasets/.venv/bin/activate
    uvicorn enwiki_remote_server:app --host 0.0.0.0 --port 8765

Env:
    ENWIKI_DB_PATH  path to enwiki.db (default: ~/datasets/enwiki/enwiki.db)
"""

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response

DB_PATH = Path(
    os.environ.get(
        "ENWIKI_DB_PATH",
        str(Path.home() / "datasets" / "enwiki" / "enwiki.db"),
    )
)

_conn: sqlite3.Connection | None = None


def _open_conn() -> sqlite3.Connection:
    """Open the enwiki DB read-only with dict-style row access."""
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _conn_or_503() -> sqlite3.Connection:
    """Return the process-wide cached connection or 503 if the DB is missing."""
    global _conn
    if _conn is None:
        try:
            _conn = _open_conn()
        except sqlite3.OperationalError as e:
            raise HTTPException(503, f"enwiki.db not available: {e}") from e
    return _conn


def _row_to_article(row: sqlite3.Row) -> dict:
    """Map an `articles` row to the dict shape the proxy router consumes."""
    return {
        "page_id": row["page_id"],
        "title": row["title"],
        "namespace": row["namespace"],
        "revision_id": row["revision_id"],
        "timestamp": row["timestamp"],
        "text_bytes": row["text_bytes"],
    }


app = FastAPI(title="enwiki remote", version="0.1.0")


@app.get("/health")
def health() -> dict:
    """Report DB reachability and article count (from db_metadata cache)."""
    try:
        conn = _conn_or_503()
        row = conn.execute(
            "SELECT value FROM db_metadata WHERE key = 'article_count'"
        ).fetchone()
        article_count = int(row["value"]) if row else None
    except HTTPException as e:
        return {"ok": False, "db_path": str(DB_PATH), "detail": e.detail}
    return {"ok": True, "db_path": str(DB_PATH), "article_count": article_count}


@app.get("/articles")
def list_articles(
    q: str | None = Query(
        None,
        description=(
            "FTS5 trigram match on title. Trigram tokeniser requires 3+ char "
            "terms. Syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    title: str | None = Query(
        None, description="Substring filter on title (case-sensitive LIKE)."
    ),
    namespace: int = Query(
        0, description="MediaWiki namespace id (0 = main article namespace)."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """List articles with namespace / title-FTS / title-substring filters."""
    conn = _conn_or_503()
    clauses: list[str] = ["articles.namespace = ?"]
    params: list = [namespace]
    if q is not None:
        # IN-subquery so the planner materialises FTS hits first and probes
        # `articles` by (namespace, page_id) — same trick as the simplewiki
        # router. A JOIN would scan FTS per article row.
        clauses.append(
            "articles.page_id IN "
            "(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
        )
        params.append(q)
    if title is not None:
        clauses.append("articles.title LIKE ?")
        params.append(f"%{title}%")
    where = "WHERE " + " AND ".join(clauses)

    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT page_id, title, namespace, revision_id, timestamp, text_bytes "
            f"FROM articles {where} "
            f"ORDER BY page_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    except sqlite3.OperationalError as e:
        # Most common cause: malformed FTS5 syntax (unbalanced quote etc.).
        raise HTTPException(400, f"bad query: {e}") from e

    return {
        "items": [_row_to_article(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# Content route comes BEFORE the detail route — FastAPI matches paths in
# registration order and both share the `/articles/{page_id}` prefix.
@app.get("/articles/{page_id}/content")
def get_article_content(page_id: int) -> Response:
    """Return the raw wikitext body for one article as text/plain."""
    conn = _conn_or_503()
    row = conn.execute(
        "SELECT text_content FROM articles WHERE page_id = ?", [page_id]
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"article {page_id} not found")
    if not row["text_content"]:
        raise HTTPException(404, "article has no body")
    return Response(content=row["text_content"], media_type="text/plain; charset=utf-8")


@app.get("/articles/{page_id}")
def get_article(page_id: int) -> dict:
    """Return metadata for one article by page_id."""
    conn = _conn_or_503()
    row = conn.execute(
        "SELECT page_id, title, namespace, revision_id, timestamp, text_bytes "
        "FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"article {page_id} not found")
    return _row_to_article(row)
