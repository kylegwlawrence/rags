"""Extract one Doc per GitHub README for the RAG indexer.

Each `readmes` row with `status = 'fetched'` and a non-empty `readme` column
is yielded as a Doc. READMEs are already Markdown, so no rendering step is
needed — the raw `readme` text is passed directly to `rag.chunker.chunk_markdown`,
which splits on `##` headings so per-chunk `section` labels (e.g. "Installation",
"Usage", "Contributing") populate from the README's own heading structure.

`doc_id` is the `repo` slug (e.g. `"sindresorhus/awesome"`).

Version key is `content_hash(readme)` plus `CLEANER_VERSION`. The source DB
has no per-row `updated_at`, so a content hash is the only edit-detection
signal; bumping `CLEANER_VERSION` invalidates all previously-indexed docs.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per fetched README row.

    Args:
        conn: Read-only connection to `data/github/readmes.db`.
        limit: Maximum number of READMEs to yield. None processes all.
    """
    sql = (
        "SELECT repo, name, readme FROM readmes "
        "WHERE status = 'fetched' AND readme IS NOT NULL AND readme != '' "
        "ORDER BY repo"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        readme: str = row["readme"]
        if not readme.strip():
            continue
        version = content_hash(readme)
        yield Doc(
            doc_id=row["repo"],
            title=row["name"] or row["repo"],
            version=f"{version}-{CLEANER_VERSION}",
            text=readme,
            section=None,
        )
