"""Extract top-N OpenAlex works for the RAG indexer.

Samples by `cited_by_count DESC` (default 5000) — embedding the full 268k
corpus is deferred. doc_id is the short W-id (after the last `/` in
`works.id`), matching what `/openalex/works/{short_id}` already uses.
"""

import sqlite3
from typing import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html

DEFAULT_LIMIT = 5000


def iter_docs(works_conn: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> Iterator[Doc]:
    """Yield Docs for the top `limit` most-cited works.

    Title and abstract are HTML-stripped and whitespace-normalised before
    composition — the OpenAlex inverted-index reconstruction in
    `openalex_download.py` leaves HTML entities (`&amp;`, `&lt;`) and the
    occasional `<a>` tag in the text. Works with both fields null after
    cleanup are skipped. `CLEANER_VERSION` is appended to the version hash so
    any cleaner change invalidates previously-stored chunks.
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
        title = normalize_whitespace(strip_html(row["title"] or ""))
        abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
        if not title and not abstract:
            continue
        if title and abstract:
            text = f"{title}\n\n{abstract}"
        else:
            text = title or abstract
        # Fallback to the W-id (not '<untitled>') so the embedder's
        # format_document header doesn't push NULL-title works toward each
        # other in vector space via a shared placeholder string.
        display_title = title or short_id
        yield Doc(
            doc_id=short_id,
            title=display_title,
            version=f"{content_hash(title, abstract)}-{CLEANER_VERSION}",
            text=text,
            section=None,
        )
