"""Extract one Doc per arxiv paper for the RAG indexer.

Phase 2a embeds title + abstract only (single chunk per paper for most rows).
Full-HTML chunking is deferred to Phase 3 alongside the OAI/render pipeline
port from `local_wikipedia`.
"""

import sqlite3
from typing import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html


def iter_docs(arxiv_conn: sqlite3.Connection, limit: int | None = None) -> Iterator[Doc]:
    """Yield one Doc per row in `arxiv.papers`, optionally capped to `limit`.

    `Doc.text` is `title + "\\n\\n" + abstract` after HTML stripping and
    whitespace normalisation. `version` is `papers.oai_datestamp` when present;
    otherwise a content hash so re-extracts still detect upstream edits when a
    paper has no OAI timestamp. `CLEANER_VERSION` is appended so any cleaner
    change invalidates every previously-stored chunk.
    """
    if limit is not None:
        cursor = arxiv_conn.execute(
            "SELECT id, title, abstract, oai_datestamp, updated_date "
            "FROM papers ORDER BY id LIMIT ?",
            (limit,),
        )
    else:
        cursor = arxiv_conn.execute(
            "SELECT id, title, abstract, oai_datestamp, updated_date "
            "FROM papers ORDER BY id"
        )
    for row in cursor:
        title = normalize_whitespace(strip_html(row["title"] or ""))
        abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
        text = f"{title}\n\n{abstract}" if title and abstract else (title or abstract)
        base_version = row["oai_datestamp"] or content_hash(title, abstract, row["updated_date"])
        yield Doc(
            doc_id=row["id"],
            title=title or row["id"],
            version=f"{base_version}-{CLEANER_VERSION}",
            text=text,
            section=None,
        )
