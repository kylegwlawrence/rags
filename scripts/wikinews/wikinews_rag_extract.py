"""Extract one Doc per wikinews article for the RAG indexer.

Reads ``articles`` rows from ``data/wikinews/wikinews.db`` filtered to
namespace 0 and yields ``Doc`` instances with the wikitext converted to
markdown via ``rag.wikitext.wikitext_to_markdown``.

Redirects and articles whose stripped body is empty are skipped silently.

Version key is ``{revision_id}-{CLEANER_VERSION}``.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.cleaner import CLEANER_VERSION
from rag.wikitext import wikitext_to_markdown


def iter_docs(
    wikinews_conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per main-namespace article, ordered by pub_date DESC then page_id.

    Args:
        wikinews_conn: Read-only connection to ``data/wikinews/wikinews.db``.
        limit: Maximum number of articles to yield. None processes the full set.
    """
    sql = (
        "SELECT page_id, title, revision_id, text_content "
        "FROM articles WHERE namespace = 0 "
        "ORDER BY pub_date DESC, page_id DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        cursor = wikinews_conn.execute(sql, (limit,))
    else:
        cursor = wikinews_conn.execute(sql)

    for row in cursor:
        wikitext = row["text_content"]
        if not wikitext:
            continue
        markdown = wikitext_to_markdown(wikitext)
        if not markdown:
            continue
        yield Doc(
            doc_id=str(row["page_id"]),
            title=row["title"],
            version=f"{row['revision_id']}-{CLEANER_VERSION}",
            text=markdown,
            section=None,
        )
