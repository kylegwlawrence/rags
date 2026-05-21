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

from api.models import Chunk, ChunksResponse
from rag import retriever
from rag.retriever import is_operational_error


def add_chunks_route(
    router: APIRouter,
    *,
    opener: Callable[[], sqlite3.Connection],
    source_name: str,
    indexer_script: str,
    rag_db_path: str | None = None,
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
        try:
            result = retriever.retrieve(
                q, rag_conn, top_k=top_k, candidate_k=candidate_k
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
            for h in result.hits
        ]
        return ChunksResponse(
            items=items,
            used_dense=result.used_dense,
            top_k=top_k,
            candidate_k=candidate_k,
        )
