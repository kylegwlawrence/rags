import sqlite3

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api._embedded import embedded_clauses
from api._fts import translate_table_errors
from api.models import Article, CategorySummary, EmbedResult, Page
from rag import Doc
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION
from rag.embed_one import embed_doc
from rag.profiles import SIMPLEWIKI as _PROFILE
from rag.wiki_render import convert_wikitext_to_html
from rag.wikitext import normalize_category, redirect_target, wikitext_to_markdown

router = APIRouter(prefix="/simplewiki", tags=["simplewiki"])

# Live-embed chunk settings come from `rag.profiles.SIMPLEWIKI` — the same
# profile that `scripts/simplewiki/simplewiki_index_rag.py` uses, so an
# article embedded via the button chunks identically to one embedded by a
# full batch indexer run.


def _row_to_article(row: sqlite3.Row) -> Article:
    """Map an `articles` row to its response model. text_content lives at /content.

    `redirect_to` is left None here; only the detail endpoint walks the
    redirect chain and overrides it. Pydantic would fill the default anyway,
    but spelling it out keeps the list-vs-detail contract obvious.
    """
    return Article(
        page_id=row["page_id"],
        title=row["title"],
        namespace=row["namespace"],
        revision_id=row["revision_id"],
        timestamp=row["timestamp"],
        text_bytes=row["text_bytes"],
        redirect_to=None,
    )


_META_COLS = "page_id, title, namespace, revision_id, timestamp, text_bytes"


def _lookup_meta(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    """Fetch an `articles` row's metadata plus a 300-char `head` of the body.

    The `head` is enough for `_resolve_redirect` to detect and follow a
    ``#REDIRECT`` stub without loading the full text_content (which can be
    several MB on long articles).
    """
    row = conn.execute(
        f"SELECT {_META_COLS}, substr(text_content, 1, 300) AS head "
        "FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return row


def _lookup_with_body(conn: sqlite3.Connection, page_id: int) -> sqlite3.Row:
    """Fetch an `articles` row including the full `text_content` or raise 404."""
    row = conn.execute(
        f"SELECT {_META_COLS}, text_content FROM articles WHERE page_id = ?",
        [page_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"article {page_id} not found")
    return row


# Cap redirect-chain length. MediaWiki disallows double redirects, but dumps
# still contain a few, plus the occasional A→B→A cycle — bound the walk so a
# bad chain can't loop forever or fan out into many queries.
_MAX_REDIRECT_HOPS = 10


def _find_by_title(conn: sqlite3.Connection, title: str) -> sqlite3.Row | None:
    """Look up a namespace-0 article by exact title, then first-letter-capitalised.

    Redirect targets in wikitext use varied casing (``[[animal]]``,
    ``[[boot]]``); MediaWiki canonicalises the first character to upper case
    while keeping the rest verbatim. Both lookups hit ``idx_articles_title``
    (BINARY collation), so each is a fast index probe rather than a scan. Only
    ``head`` (a prefix of the body) is selected so chain-following never loads a
    multi-megabyte article body just to test whether the target is itself a
    redirect.
    """
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
    """Follow ``start_text``'s redirect chain to the final target page_id.

    Returns None when the article isn't a redirect, the target title can't be
    matched (broken redirect), or the chain cycles / exceeds the hop cap — in
    every "can't resolve" case the caller falls back to showing the raw stub.
    """
    target = redirect_target(start_text)
    if target is None:
        return None

    visited = {start_page_id}
    for _ in range(_MAX_REDIRECT_HOPS):
        # Titles are stored with spaces; wikitext targets may use underscores.
        row = _find_by_title(conn, target.replace("_", " ").strip())
        if row is None:
            return None
        page_id = row["page_id"]
        if page_id in visited:
            return None  # cycle
        visited.add(page_id)
        next_target = redirect_target(row["head"])
        if next_target is None:
            return page_id  # reached a real article
        target = next_target
    return None  # chain too long


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
    category: str | None = Query(
        None,
        description=(
            "Substring filter (case-insensitive): articles in any category "
            "whose name contains this text (e.g. 'actor' matches 'American "
            "movie actors'). Underscores/whitespace are normalized like the "
            "stored names. Backed by the page_categories table from "
            "simplewiki_index_categories.py. Combine with q/title to search "
            "within categories. See /simplewiki/categories for the full list."
        ),
    ),
    embedded: bool | None = Query(
        None,
        description=(
            "Filter by RAG embedding state: true = only articles with chunks "
            "in simplewiki_rag.db, false = only articles not yet embedded. "
            "Omit to list all (the default)."
        ),
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
    if category is not None:
        # Substring match: normalize the query the same way the stored names
        # were built (underscores->spaces, trim) so formatting doesn't cause
        # misses, then LIKE %…% (case-insensitive for ASCII). IN-subquery over
        # page_categories resolves the page_id allowlist first, then probes
        # articles by (namespace, page_id); IN dedupes pages hit via multiple
        # matching categories.
        clauses.append(
            "articles.page_id IN "
            "(SELECT page_id FROM page_categories WHERE category LIKE ?)"
        )
        params.append(f"%{normalize_category(category)}%")
    if embedded is not None:
        # docs_meta stores stringified page_ids; cast to int since
        # articles.page_id is INTEGER.
        c, p, empty = embedded_clauses(
            db.simplewiki_rag,
            embedded=embedded,
            column="articles.page_id",
            id_transform=int,
        )
        if empty:
            return Page[Article](items=[], total=0, limit=limit, offset=offset)
        clauses.extend(c)
        params.extend(p)
    where = "WHERE " + " AND ".join(clauses)

    with translate_table_errors("simplewiki", "simplewiki_parse.py", "data/simplewiki/simplewiki.db"):
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


@router.get("/categories", response_model=Page[CategorySummary])
def list_categories(
    q: str | None = Query(
        None,
        description="Substring filter on category name (case-insensitive LIKE).",
    ),
    sort: str = Query(
        "count",
        pattern="^(count|name)$",
        description="Order by article count (desc, default) or category name (asc).",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Page[CategorySummary]:
    """List distinct categories with their article counts.

    Built from the page_categories table (simplewiki_index_categories.py). A 503
    here means that table hasn't been built yet. Use a name from this list to
    drive the `?category=` filter on /simplewiki/articles.
    """
    clauses: list[str] = []
    params: list = []
    if q is not None:
        clauses.append("category LIKE ?")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "ORDER BY n DESC, category ASC" if sort == "count" else "ORDER BY category ASC"

    with translate_table_errors(
        "simplewiki", "simplewiki_index_categories.py", "data/simplewiki/simplewiki.db"
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


@router.get("/resolve", response_model=Article)
def resolve_title(
    title: str = Query(
        ..., description="Exact article title to resolve to a page (for [[wikilink]] navigation)."
    ),
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Article:
    """Resolve a namespace-0 article title to its row (fast, index-backed).

    Powers in-app [[wikilink]] navigation: the frontend resolves the link's
    title here, then opens the returned page_id (redirects are followed by the
    normal detail path). `INDEXED BY idx_articles_title` forces the title index
    so this is a probe, not a scan, regardless of the planner's stats.
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


# Content route comes BEFORE the detail route — same reason as the arxiv
# router: route matching is order-sensitive and both share the {page_id}
# prefix.
@router.get("/articles/{page_id}/content")
def get_article_content(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Response:
    """Render one article's wikitext to HTML for the Content view.

    `rag.wiki_render.convert_wikitext_to_html` handles the messy-wikitext
    cases (infoboxes, templates, refs, tables, math) and falls back to escaped
    plaintext on its own if a render bug is hit, so this never 500s.
    """
    row = _lookup_with_body(conn, page_id)
    if not row["text_content"]:
        raise HTTPException(status_code=404, detail="article has no body")
    html = convert_wikitext_to_html(row["text_content"])
    return Response(content=html, media_type="text/html; charset=utf-8")


@router.get("/articles/{page_id}", response_model=Article)
def get_article(
    page_id: int,
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> Article:
    """Return metadata for one article by page_id.

    When the article is a ``#REDIRECT`` stub, ``redirect_to`` carries the final
    resolved target's page_id so the UI can navigate straight there. It stays
    None for normal articles and for redirects whose target can't be resolved.
    """
    row = _lookup_meta(conn, page_id)
    article = _row_to_article(row)
    # `head` is the first 300 chars — enough for redirect_target / _resolve_redirect
    # to recognise and follow a #REDIRECT stub without loading the full body.
    article.redirect_to = _resolve_redirect(conn, row["head"] or "", page_id)
    # Attach this article's categories. The table is optional, so a missing
    # page_categories just yields no categories rather than failing the lookup.
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
    conn: sqlite3.Connection = Depends(db.simplewiki),
) -> EmbedResult:
    """Embed one article into simplewiki_rag.db on demand (synchronous).

    Renders the article's wikitext to markdown via the same path as
    `simplewiki_index_rag.py` and replaces any chunks already stored for it, so
    the article becomes searchable through `/simplewiki/chunks` and visible in
    `/simplewiki/doc-chunks` straight away — the RAG DB runs in WAL mode, so the
    cached read-only connection picks up the new rows without a uvicorn restart.

    A single article is ~1-5 chunks, a few seconds on local Ollama, so this
    blocks the request rather than queueing a job. Redirects / empty bodies
    embed nothing and return `embedded=false`. A 503 means Ollama was
    unreachable; the article's existing chunks (if any) are left untouched.
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

    rag_conn = db.connect_rag_rw(db.SIMPLEWIKI_RAG_DB)
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
