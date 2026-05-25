"""Thin proxy for an enwiki SQLite DB served by a remote FastAPI host.

The matching server lives in `scripts/enwiki/enwiki_remote_server.py` and runs
on a separate host (typically raspberrypi6) because the enwiki DB is ~76 GB
and would dominate this machine's storage. Every request here forwards to
that host over Tailscale via `ENWIKI_REMOTE_URL` and reshapes the response
into the same `Page[Article]` / `Article` models the rest of the API uses,
so the frontend treats this source like any other.

The embed button (`POST /articles/{page_id}/embed`) fetches the article body
from the remote and embeds it locally into `data/enwiki/enwiki_rag.db` using
local Ollama — no writes to the Pi. Chunks are served from that local RAG DB.

A missing `ENWIKI_REMOTE_URL` env var or an unreachable host both surface as
503 with a clear `detail`, matching the per-DB 503 pattern in `api.db`.
"""

import os

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from api import db
from api._chunks import add_chunks_route, add_doc_chunks_route
from api.models import Article, EmbedResult, Page
from rag import Doc
from rag.chunker import chunk_markdown
from rag.cleaner import CLEANER_VERSION
from rag.embed_one import embed_doc
from rag.profiles import ENWIKI as _PROFILE
from rag.wikitext import wikitext_to_markdown

router = APIRouter(prefix="/enwiki", tags=["enwiki"])

# Resolved at import time. Restart uvicorn after changing the env var, same as
# the rest of the API (its DB connections are also bound at module load).
REMOTE_URL = (os.environ.get("ENWIKI_REMOTE_URL") or "").rstrip("/")

# Generous timeout: the remote runs on a Pi and a content fetch can pull a
# multi-MB wikitext blob over Tailscale. 30 s matches the simplewiki live-embed
# httpx timeout pattern elsewhere in the API.
_TIMEOUT = httpx.Timeout(30.0)


def _require_remote() -> str:
    """Return the configured remote base URL or 503 if it's not set."""
    if not REMOTE_URL:
        raise HTTPException(
            status_code=503,
            detail=(
                "ENWIKI_REMOTE_URL is not set; cannot reach enwiki host. "
                "Set it to e.g. http://raspberrypi6:8765 and restart uvicorn."
            ),
        )
    return REMOTE_URL


def _raise_for_remote(resp: httpx.Response) -> None:
    """Re-raise a non-2xx remote response as an HTTPException with its detail.

    The remote uses FastAPI too, so error bodies are `{"detail": "..."}`. Pass
    that through verbatim so the caller sees the same message they'd see if
    they hit the remote directly; fall back to raw text if it isn't JSON.
    """
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except ValueError:
        detail = resp.text
    raise HTTPException(status_code=resp.status_code, detail=detail)


def _get(path: str, params: dict | None = None) -> httpx.Response:
    """GET `path` on the remote, translating connection errors to 503."""
    base = _require_remote()
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            return client.get(f"{base}{path}", params=params)
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503, detail=f"enwiki remote unreachable: {e}"
        ) from e


@router.get("/articles", response_model=Page[Article])
def list_articles(
    q: str | None = Query(
        None,
        description=(
            "FTS5 trigram match on title. Trigram tokeniser requires 3+ char "
            "terms. Syntax: `\"phrase\"`, `term*`, `a OR b`, `a NOT b`."
        ),
    ),
    title: str | None = Query(
        None, description="Substring filter on title (LIKE, case-sensitive)."
    ),
    namespace: int = Query(
        0, description="MediaWiki namespace id (0 = main article namespace)."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[Article]:
    """List enwiki articles via the remote service."""
    params: dict = {"namespace": namespace, "limit": limit, "offset": offset}
    if q is not None:
        params["q"] = q
    if title is not None:
        params["title"] = title
    resp = _get("/articles", params=params)
    _raise_for_remote(resp)
    return Page[Article].model_validate(resp.json())


# Content route comes BEFORE the detail route — FastAPI matches paths in
# registration order and both share the `/articles/{page_id}` prefix.
@router.get("/articles/{page_id}/content")
def get_article_content(page_id: int) -> Response:
    """Return the raw wikitext body for one article as text/plain."""
    resp = _get(f"/articles/{page_id}/content")
    _raise_for_remote(resp)
    return Response(content=resp.content, media_type="text/plain; charset=utf-8")


@router.post("/articles/{page_id}/embed", response_model=EmbedResult)
def embed_article(page_id: int) -> EmbedResult:
    """Embed one enwiki article into enwiki_rag.db on demand (synchronous).

    Fetches the article's metadata and wikitext from the remote Pi server,
    renders wikitext to markdown, and embeds with local Ollama — identical
    pipeline to the simplewiki embed button. Writes to a local enwiki_rag.db
    (WAL mode; readable by the cached read-only connection immediately).

    Two HTTP calls to the Pi: one for metadata (title, revision_id), one for
    the wikitext body. A 503 means either the Pi is unreachable or Ollama is
    down; any prior chunks for this article are left untouched in that case.
    """
    meta_resp = _get(f"/articles/{page_id}")
    _raise_for_remote(meta_resp)
    meta = meta_resp.json()

    body_resp = _get(f"/articles/{page_id}/content")
    _raise_for_remote(body_resp)

    markdown = wikitext_to_markdown(body_resp.text)
    doc = Doc(
        doc_id=str(page_id),
        title=meta["title"],
        version=f"{meta['revision_id']}-{CLEANER_VERSION}",
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
def get_article(page_id: int) -> Article:
    """Return metadata for one enwiki article via the remote."""
    resp = _get(f"/articles/{page_id}")
    _raise_for_remote(resp)
    return Article.model_validate(resp.json())


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
