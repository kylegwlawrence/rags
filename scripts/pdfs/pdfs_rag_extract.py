"""Extract one Doc per ingested PDF for the RAG indexer.

Per-PDF Doc construction and the page-aware chunker live in `rag.pdfs`
(`build_doc` / `chunk_pdf`) so the API's live-embed route can reuse them
identically — same split as `rag.eurlex` vs `scripts/eurlex/eurlex_rag_extract.py`.
This module is just the indexer entry point: it lists the PDFs and delegates to
that builder. `chunk_pdf` is re-exported so `pdfs_index_rag.py` can pass it as
the indexer's `chunk_fn` without importing from two places.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.pdfs import build_doc, chunk_pdf  # noqa: F401  (chunk_pdf re-exported)


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per PDF with extractable text, newest first.

    Args:
        conn: Read-only connection to `data/pdfs/pdfs.db`.
        limit: Maximum number of PDFs to yield. None processes all.
    """
    sql = "SELECT doc_id FROM documents ORDER BY ingested_at DESC, doc_id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql).fetchall():
        doc = build_doc(conn, row["doc_id"])
        if doc is not None:
            yield doc
