"""Extract one Doc per OpenStax section for the RAG indexer.

Per-section Doc construction lives in `rag.openstax.build_doc` so the API's
live-embed route can reuse it identically — the same split as
`rag.eurlex` vs `scripts/eurlex/eurlex_rag_extract.py`. This module is just the
indexer entry point: it queries the `sections` table (joined to `books` for the
book title) in reading order and delegates each row to that builder.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.openstax import build_doc

# Sections joined to their book title, in book → reading order. The book title
# becomes the Doc title (the embedder prefixes it); the rest feed build_doc.
_SELECT = """
    SELECT s.section_id, b.title AS book_title, s.chapter_title,
           s.title, s.objectives, s.body
    FROM sections s
    JOIN books b ON b.book_id = s.book_id
    ORDER BY s.book_id, s.seq
"""


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per section with body text, in reading order.

    Args:
        conn: Read-only connection to `data/openstax/openstax.db`.
        limit: Maximum number of sections to yield. None processes all.
    """
    sql = _SELECT
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    for row in conn.execute(sql).fetchall():
        doc = build_doc(row)
        if doc is not None:
            yield doc
