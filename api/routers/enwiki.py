"""Read-only routes over the local enwiki SQLite DB.

The full ~263 GB `enwiki.db` now lives on this machine at `data/enwiki/enwiki.db`,
so these routes query it directly — the same plain-SQL pattern as the simplewiki
router. (Earlier versions proxied every request to a FastAPI service on
raspberrypi6 because the DB was too big to keep locally; that proxy is gone.)

The local `articles_fts` index covers both `title` and `text_content`, so `?q=`
is a full-text match over title *and* body — unlike simplewiki/the old Pi index,
which were title-only.

The embed button (`POST /articles/{page_id}/embed`) renders the article's
wikitext to markdown and embeds it into `data/enwiki/enwiki_rag.db` via local
Ollama. Chunks are served from that local RAG DB.
"""

import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._fts import translate_table_errors
from api.models import Article, EmbedResult, Page
from rag import Doc
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION
from rag.embed_one import embed_doc
from rag.profiles import ENWIKI as _PROFILE
from rag.wiki_render import convert_wikitext_to_html
from rag.wikitext import wikitext_to_markdown

router = APIRouter(prefix="/enwiki", tags=["enwiki"])

# Live-embed chunk settings come from `rag.profiles.ENWIKI` (== SIMPLEWIKI), so
# an article embedded via the button chunks identically to the batch indexer.

_META_COLS = "page_id, title, namespace, revision_id, timestamp, text_bytes"

# Pin the missing-table / unreadable-DB 503 hint for this source. enwiki.db is a
# prebuilt artifact (no local indexer script in this repo), so the actionable
# hint is "restore the file" rather than "run a script".
_FTS = ("enwiki", "enwiki/ (enwiki.db is prebuilt — restore it)", "data/enwiki/enwiki.db")


def _row_to_article(row: sqlite3.Row) -> Article:
    """Map an `articles` row to its response model. text_content lives at /content."""
    return Article(
        page_id=row["page_id"],
        title=row["title"],
        namespace=row["namespace"],
        revision_id=row["revision_id"],
        timestamp=row["timestamp"],
        text_bytes=row["text_bytes"],
        redirect_to=None,
    )


@router.get("/articles", response_model=Page[Article])
def list_articles(
    q: str | None = Query(
        None,
        description=(
            "FTS5 trigram match over title AND body. Trigram tokeniser requires "
            "3+ char terms. Syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`. "
            "Use `title:term` to restrict the match to the title column."
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
    conn: sqlite3.Connection = Depends(db.enwiki),
) -> Page[Article]:
    """List enwiki articles with namespace / title-substring / FTS5 filters."""
    clauses: list[str] = ["articles.namespace = ?"]
    params: list = [namespace]
    if q is not None:
        # IN-subquery (not JOIN) so the planner materialises FTS hits first and
        # probes `articles` by (namespace, page_id) — same trick as simplewiki.
        # articles_fts has content_rowid=rowid aligned with articles.page_id.
        clauses.append(
            "articles.page_id IN "
            "(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
        )
        params.append(q)
    if title is not None:
        clauses.append("articles.title LIKE ?")
        params.append(f"%{title}%")
    where = "WHERE " + " AND ".join(clauses)

    with translate_table_errors(*_FTS):
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_META_COLS} FROM articles {where} "
            f"ORDER BY page_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    return Page[Article](
        items=[_row_to_article(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/resolve", response_model=Article)
def resolve_title(
    title: str = Query(
        ..., description="Exact article title to resolve to a page (for [[wikilink]] navigation)."
    ),
    conn: sqlite3.Connection = Depends(db.enwiki),
) -> Article:
    """Resolve a namespace-0 article title to its row (fast, index-backed).

    Powers in-app [[wikilink]] navigation. `INDEXED BY idx_articles_title`
    forces the title index — without it the planner picks idx_articles_namespace
    and scans ~19M rows (namespace 0 is almost the whole corpus).
    """
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


# Content route comes BEFORE the detail route — FastAPI matches paths in
# registration order and both share the `/articles/{page_id}` prefix.
@router.get("/articles/{page_id}/content")
def get_article_content(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.enwiki),
) -> Response:
    """Render one article's wikitext to HTML for the Content view.

    Same display renderer as simplewiki (`rag.wiki_render`); it falls back to
    escaped plaintext internally on a render error, so this never 500s.
    """
    row = conn.execute(
        "SELECT text_content FROM articles WHERE page_id = ?", [page_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    if not row["text_content"]:
        raise HTTPException(status_code=404, detail="article has no body")
    html = convert_wikitext_to_html(row["text_content"])
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.post("/articles/{page_id}/embed", response_model=EmbedResult)
def embed_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.enwiki),
) -> EmbedResult:
    """Embed one enwiki article into enwiki_rag.db on demand (synchronous).

    Reads the article's metadata and wikitext from the local DB, renders
    wikitext to markdown, and embeds with local Ollama — identical pipeline to
    the simplewiki embed button. Writes to a local enwiki_rag.db (WAL mode;
    readable by the cached read-only connection immediately). Only namespace-0
    (main) articles can be embedded. A 503 means Ollama was unreachable; any
    prior chunks for this article are left untouched.
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, text_content FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    if row["namespace"] != 0:
        raise HTTPException(
            status_code=422,
            detail=(
                f"article {page_id} is in namespace {row['namespace']}; only "
                "namespace 0 (main articles) can be embedded"
            ),
        )

    markdown = wikitext_to_markdown(row["text_content"] or "")
    doc = Doc(
        doc_id=str(row["page_id"]),
        title=row["title"],
        version=f"{row['revision_id']}-{CLEANER_VERSION}",
        text=markdown,
        section=None,
    )

    rag_conn = db.connect_rag_rw(db.ENWIKI_RAG_DB)
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


@router.get("/articles/{page_id}", response_model=Article)
def get_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.enwiki),
) -> Article:
    """Return metadata for one enwiki article by page_id."""
    row = conn.execute(
        f"SELECT {_META_COLS} FROM articles WHERE page_id = ?", [page_id]
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return _row_to_article(row)


add_chunks_route(
    router,
    opener=db.enwiki_rag,
    source_name="enwiki",
    indexer_script="enwiki_index_rag.py",
)
add_doc_chunks_route(
    router,
    opener=db.enwiki_rag,
    source_name="enwiki",
    indexer_script="enwiki_index_rag.py",
)
