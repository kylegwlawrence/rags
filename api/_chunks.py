"""Shared `/chunks` and `/doc-chunks` endpoint factory for per-source routers."""

import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from api.models import Chunk, ChunksResponse, StoredChunk
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

    Empty `q` → 400; missing rag.db/tables → 503 with `indexer_script` hint;
    Ollama down → 200 with `used_dense=False`. Pass `rag_db_path` explicitly
    when the filename doesn't follow the `<source_name>_rag.db` convention.
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
            for h in result.hits[:top_k]
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

    Returns all stored chunks for a `doc_id` in document order (by chunk_id,
    which is insertion/reading order). Returns `[]` for unindexed docs.
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
