"""Single-document embed for the API's live "Embed" button.

Chunks one Doc, embeds, replaces prior rows, syncs FTS incrementally, and commits.
Caller owns the read-write connection (open with rag.schema.connect_rag).
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
    """Chunk, embed, and write one Doc. Embed happens before any insert so no partial state on error.
    Returns chunk count (0 means empty body — prior rows still removed). Raises httpx.HTTPError on Ollama failure.
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
