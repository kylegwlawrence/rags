"""Extract top-N OpenAlex works for the RAG indexer.

Samples by `cited_by_count DESC` (default 5000) — embedding the full 268k
corpus is deferred. doc_id is the short W-id (after the last `/` in
`works.id`), matching what `/openalex/works/{short_id}` already uses.
"""

import hashlib
import sqlite3
from typing import Iterator

from rag import Doc

DEFAULT_LIMIT = 5000


def iter_docs(works_conn: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> Iterator[Doc]:
    """Yield Docs for the top `limit` most-cited works.

    Works with both `title IS NULL` and `abstract IS NULL` are skipped (nothing
    to embed). Otherwise text is `f"{title}\\n\\n{abstract}"` with each part
    omitted when null.
    """
    cursor = works_conn.execute(
        "SELECT id, title, abstract "
        "FROM works "
        "WHERE title IS NOT NULL OR abstract IS NOT NULL "
        "ORDER BY cited_by_count DESC "
        "LIMIT ?",
        (limit,),
    )
    for row in cursor:
        full_id = row["id"]
        short_id = full_id.rsplit("/", 1)[-1] if full_id else full_id
        title = row["title"] or ""
        abstract = row["abstract"] or ""
        if title and abstract:
            text = f"{title}\n\n{abstract}"
        else:
            text = title or abstract
        # Use title (or '<no title>' fallback) for the embedder's format_document
        # header — it goes into the document vector as provenance.
        display_title = title or "<untitled>"
        yield Doc(
            doc_id=short_id,
            title=display_title,
            version=_content_hash(title, abstract),
            text=text,
            section=None,
        )


def _content_hash(*parts: str | None) -> str:
    """SHA-256 hex prefix of joined parts; nulls become ''."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]
