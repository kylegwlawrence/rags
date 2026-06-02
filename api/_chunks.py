"""Shared `/chunks` endpoint factory for the per-source routers.

Three (and now four) sources expose identical `/chunks` endpoints — same
query parameters, same hybrid-retrieval call, same error semantics, same
response shape. Only the dependency function and the 503 detail strings
vary. This module is the WORK.md §3.6 extraction (three similar
implementations is the threshold).
"""

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from api.models import Chunk, ChunksResponse, StoredChunk
from rag import Hit, retriever
from rag.retriever import is_operational_error


def add_chunks_route(
    router: APIRouter,
    *,
    opener: Callable[[], sqlite3.Connection],
    source_name: str,
    indexer_script: str,
    rag_db_path: str | None = None,
    hit_filter: Callable[[list[Hit]], list[Hit]] | None = None,
) -> None:
    """Attach `GET /chunks` to `router`.

    Runs hybrid (FTS5 + sqlite-vec) retrieval against the source's `_rag.db`
    via `rag.retriever.retrieve`. Empty `q` → 400; missing rag.db / index
    tables → 503 naming `indexer_script`; Ollama unreachable → 200 with
    `used_dense=False`.

    Args:
        router: The source's existing APIRouter (already mounted at `/<source>`).
        opener: Cached read-only connection getter from `api.db`
            (e.g. `db.arxiv_rag`). Loads sqlite-vec on first call.
        source_name: Short source name; appears in the 503 detail body.
        indexer_script: Filename of the script that rebuilds this RAG DB,
            named in the 503 detail so the caller knows what to run.
        rag_db_path: Repo-relative path to the source's `_rag.db`, named in
            the 503 detail as a restore hint. Defaults to
            `data/<source_name>/<source_name>_rag.db`; pass explicitly when
            the file name doesn't match the source name (e.g. pydocs uses
            `python_docs_rag.db`).
        hit_filter: Optional post-retrieval filter that drops hits before they
            become the page. Used by arxiv to drop chunks whose paper lives in
            an archived (no-longer-on-disk) shard — the global `arxiv_rag.db`
            still holds them but they can't be served. When set, the route
            over-fetches (retrieves up to `candidate_k` merged hits instead of
            `top_k`) so dropped hits don't shrink the page below `top_k`.
    """
    if rag_db_path is None:
        rag_db_path = f"data/{source_name}/{source_name}_rag.db"

    @router.get("/chunks", response_model=ChunksResponse)
    def search_chunks(
        q: str = Query(..., description="Natural-language query. Empty → 400."),
        top_k: int = Query(20, ge=1, le=100),
        candidate_k: int = Query(50, ge=10, le=200),
        rag_conn: sqlite3.Connection = Depends(opener),
    ) -> ChunksResponse:
        if not q.strip():
            raise HTTPException(status_code=400, detail="q must not be empty")
        # With a hit_filter, retrieve the full merged candidate pool (capped by
        # candidate_k) so that filtering still leaves up to top_k survivors;
        # without one, top_k is all we need.
        retrieve_k = max(top_k, candidate_k) if hit_filter else top_k
        try:
            result = retriever.retrieve(
                q, rag_conn, top_k=retrieve_k, candidate_k=candidate_k
            )
        except sqlite3.OperationalError as e:
            if is_operational_error(e):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"{source_name} RAG data not ready ({e}). "
                        f"Run scripts/{indexer_script} or restore {rag_db_path}."
                    ),
                ) from e
            raise HTTPException(status_code=400, detail=f"bad query: {e}") from e

        hits = result.hits
        if hit_filter is not None:
            hits = hit_filter(hits)
        hits = hits[:top_k]

        items = [
            Chunk(
                chunk_id=h.chunk_id,
                doc_id=h.doc_id,
                title=h.title,
                section=h.section,
                chunk_index=h.chunk_index,
                text=h.text,
                text_length=h.text_length,
                score=h.score,
            )
            for h in hits
        ]
        return ChunksResponse(
            items=items,
            used_dense=result.used_dense,
            top_k=top_k,
            candidate_k=candidate_k,
        )


def add_doc_chunks_route(
    router: APIRouter,
    *,
    opener: Callable[[], sqlite3.Connection],
    source_name: str,
    indexer_script: str,
    rag_db_path: str | None = None,
) -> None:
    """Attach `GET /doc-chunks` to `router`.

    Returns all stored chunks for a specific `doc_id` in document order.
    Orders by `chunk_id` (autoincrement insertion order) rather than
    `chunk_index`: chunks are inserted in reading order, so `chunk_id` is the
    reliable document-order key even for rows indexed before `chunk_markdown`
    switched to a global `chunk_index`. Intended for per-document inspection,
    not retrieval. Empty result (doc not yet indexed) returns `[]`, not 404.

    Args:
        router: The source's existing APIRouter.
        opener: Cached read-only RAG connection getter from `api.db`.
        source_name: Short source name for 503 detail messages.
        indexer_script: Filename of the script that builds this RAG DB.
        rag_db_path: Repo-relative path to the `_rag.db` file.
    """
    if rag_db_path is None:
        rag_db_path = f"data/{source_name}/{source_name}_rag.db"

    @router.get("/doc-chunks", response_model=list[StoredChunk])
    def get_doc_chunks(
        doc_id: str = Query(..., description="Document ID whose chunks to fetch."),
        rag_conn: sqlite3.Connection = Depends(opener),
    ) -> list[StoredChunk]:
        try:
            rows = rag_conn.execute(
                """
                SELECT chunk_id, doc_id, section, chunk_index, text, text_length
                FROM chunks
                WHERE doc_id = ?
                ORDER BY chunk_id
                """,
                (doc_id,),
            ).fetchall()
        except sqlite3.OperationalError as e:
            if is_operational_error(e):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"{source_name} RAG data not ready ({e}). "
                        f"Run scripts/{indexer_script} or restore {rag_db_path}."
                    ),
                ) from e
            raise
        return [
            StoredChunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                section=row["section"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                text_length=row["text_length"],
            )
            for row in rows
        ]
