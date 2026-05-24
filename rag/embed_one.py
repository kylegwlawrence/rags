"""Synchronous single-document embed for live API requests.

The batch indexer (`rag.indexer.run_indexer`) embeds many docs per Ollama
call and rebuilds the FTS index once at the end — the right shape for a
full-corpus pass. This module is its single-doc counterpart, used by the
API's "embed this article" button: it chunks one `Doc`, embeds its chunks,
replaces any rows that already exist for the same `doc_id`, syncs the FTS
index incrementally, and commits before returning.

The caller owns the read-write connection (open it with
`rag.schema.connect_rag`) and is responsible for closing it.
"""

import sqlite3
from collections.abc import Callable

from rag import Doc, embedder
from rag.chunker import chunk_doc
from rag.schema import delete_doc_chunks


def embed_doc(
    rag_conn: sqlite3.Connection,
    doc: Doc,
    *,
    chunk_fn: Callable[..., list[dict]] = chunk_doc,
    chunk_size: int = 1500,
    overlap: int = 150,
    max_chunk_size: int | None = None,
    ollama_url: str = embedder.OLLAMA_URL,
) -> int:
    """Chunk, embed, and store one Doc, replacing any prior rows for its doc_id.

    Insert order is `docs_meta` → `chunks` → `chunks_vec`/`chunks_fts` so the
    `chunks.doc_id REFERENCES docs_meta(doc_id)` foreign key stays satisfied
    (the writer connection enables `foreign_keys = ON`). FTS is synced per
    chunk rather than bulk-rebuilt — at one document the incremental cost is
    trivial and avoids re-tokenising the whole index.

    Args:
        rag_conn: Read-write `<source>_rag.db` connection from
            `rag.schema.connect_rag`. The caller closes it.
        doc: The document to embed. Its `version` is stamped into `docs_meta`
            so a later batch-indexer run skips it when unchanged.
        chunk_fn: `chunk_doc` (prose) or `chunk_markdown` (section-aware) —
            pass whichever the source's batch indexer uses so a button-embedded
            document chunks identically to a batch-indexed one.
        chunk_size, overlap, max_chunk_size: Chunker settings; match the
            source's indexer script defaults for the same reason.
        ollama_url: Override the embedder's base URL.

    Returns:
        Number of chunks embedded. 0 when the doc yields no chunks (e.g. a
        redirect or empty body) — any prior rows are still removed in that case.

    Raises:
        httpx.HTTPError: If Ollama is unreachable after retries. The caller
            maps this to a 503. No partial state is written: the embed call
            happens before any insert.
    """
    chunks = chunk_fn(
        doc, chunk_size=chunk_size, overlap=overlap, max_chunk_size=max_chunk_size
    )

    if not chunks:
        delete_doc_chunks(rag_conn, doc.doc_id, sync_fts=True)
        rag_conn.commit()
        return 0

    texts = [
        embedder.format_document(doc.title, c["section"], c["text"]) for c in chunks
    ]
    vectors = embedder.embed_texts_batch(texts, base_url=ollama_url)
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embed returned {len(vectors)} vectors for {len(chunks)} chunks"
        )

    # Embedding succeeded — now it's safe to mutate the DB.
    delete_doc_chunks(rag_conn, doc.doc_id, sync_fts=True)
    rag_conn.execute(
        "INSERT INTO docs_meta(doc_id, version, title, chunk_count, indexed_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (doc.doc_id, doc.version, doc.title, len(chunks)),
    )
    for chunk, vec in zip(chunks, vectors, strict=True):
        cur = rag_conn.execute(
            "INSERT INTO chunks(doc_id, section, chunk_index, text, text_length) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                doc.doc_id,
                chunk["section"],
                chunk["chunk_index"],
                chunk["text"],
                chunk["text_length"],
            ),
        )
        chunk_id = cur.lastrowid
        rag_conn.execute(
            "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
            (chunk_id, chunk["text"]),
        )
        rag_conn.execute(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, embedder.pack_embedding(vec)),
        )
    rag_conn.commit()
    return len(chunks)
