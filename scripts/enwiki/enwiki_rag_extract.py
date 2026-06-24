"""Extract one Doc per enwiki article for the RAG indexer.

Reads `articles` rows from `data/enwiki/enwiki.db` filtered to namespace 0
(main article namespace) and yields `Doc` instances with the wikitext
converted to markdown via `rag.wikitext.wikitext_to_markdown`.

Redirects (``#REDIRECT [[...]]``) and articles whose stripped body is empty
are skipped silently — they would contribute zero useful chunks.

Version key is ``{revision_id}-{CLEANER_VERSION}``: enwiki revision_ids are
monotonic per page, so the re-run skip logic in `rag.indexer` will skip every
article whose revision hasn't changed since the last indexer pass. Bumping
`CLEANER_VERSION` forces a full re-embed.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.cleaner import CLEANER_VERSION
from rag.wikitext import wikitext_to_markdown


def iter_docs(
    enwiki_conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per main-namespace article, ordered by page_id.

    Args:
        enwiki_conn: Read-only connection to `data/enwiki/enwiki.db`.
        limit: Maximum number of articles to yield. None processes the full
            namespace-0 set (~19M articles for a current enwiki dump — embedding
            that on local Ollama would take years, so pass a bounded limit).
    """
    if limit is not None:
        cursor = enwiki_conn.execute(
            "SELECT page_id, title, revision_id, text_content "
            "FROM articles WHERE namespace = 0 "
            "ORDER BY page_id LIMIT ?",
            (limit,),
        )
    else:
        cursor = enwiki_conn.execute(
            "SELECT page_id, title, revision_id, text_content "
            "FROM articles WHERE namespace = 0 "
            "ORDER BY page_id"
        )
    for row in cursor:
        wikitext = row["text_content"]
        if not wikitext:
            continue
        markdown = wikitext_to_markdown(wikitext)
        if not markdown:
            # Redirect, empty body, or strip_code dropped everything.
            continue
        yield Doc(
            doc_id=str(row["page_id"]),
            title=row["title"],
            version=f"{row['revision_id']}-{CLEANER_VERSION}",
            text=markdown,
            section=None,
        )
