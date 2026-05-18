"""Extract one Doc per arxiv paper for the RAG indexer.

Phase 2a embeds title + abstract only (single chunk per paper for most rows).
Full-HTML chunking is deferred to Phase 3 alongside the OAI/render pipeline
port from `local_wikipedia`.
"""

import hashlib
import sqlite3
from typing import Iterator

from rag import Doc


def iter_docs(arxiv_conn: sqlite3.Connection) -> Iterator[Doc]:
    """Yield one Doc per row in `arxiv.papers`.

    `Doc.text` is `title + "\\n\\n" + abstract`. `version` is `papers.oai_datestamp`
    when present; otherwise a content hash so re-extracts still detect upstream
    edits when a paper has no OAI timestamp.
    """
    cursor = arxiv_conn.execute(
        "SELECT id, title, abstract, oai_datestamp, updated_date "
        "FROM papers ORDER BY id"
    )
    for row in cursor:
        title = row["title"]
        abstract = row["abstract"]
        text = f"{title}\n\n{abstract}"
        version = row["oai_datestamp"] or _content_hash(title, abstract, row["updated_date"])
        yield Doc(
            doc_id=row["id"],
            title=title,
            version=version,
            text=text,
            section=None,
        )


def _content_hash(*parts: str | None) -> str:
    """SHA-256 hex prefix of joined parts. `None` and missing parts become ''."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]
