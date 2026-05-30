"""Extract one Doc per Federal Register document for the RAG indexer.

Per-row Doc construction lives in `rag.federal_register.build_doc` so the
API's live-embed route can reuse it identically. This module is the indexer
entry point: it queries `documents` and delegates to that builder.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.federal_register import build_doc


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per row in `federal_register.documents`.

    Args:
        conn: Read-only connection to `data/federal_register/federal_register.db`.
        limit: Maximum number of documents to yield. None processes all.
    """
    sql = (
        "SELECT document_number, title, abstract, type, publication_date, "
        "       agencies, action, effective_date, excerpts "
        "FROM documents ORDER BY publication_date DESC, document_number"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        doc = build_doc(row)
        if doc is not None:
            yield doc
