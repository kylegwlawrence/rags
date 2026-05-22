"""Extract one Doc per fetched SEC EDGAR filing for the RAG indexer.

Each `filings` row with `status = 'fetched'` and a non-empty `body` is yielded
as a Doc, newest first. The body was already HTML-stripped at fetch time
(`sec_edgar_fetch_bodies.py`); strip_html runs again here as a cheap, idempotent
guard so no stray markup reaches the embedder. Filing text is flat prose with
no reliable `##` heading structure, so the indexer pairs this with
`rag.chunker.chunk_doc` rather than `chunk_markdown`.

`doc_id` is the `accession_number` (e.g. `0001234567-24-000001`).

Version key is `content_hash(body)` plus `CLEANER_VERSION`. The source has no
per-row content hash, so a hash of the body is the only edit-detection signal;
bumping `CLEANER_VERSION` invalidates all previously-indexed docs.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, strip_html


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per fetched filing, newest first.

    Args:
        conn: Read-only connection to `data/sec_edgar/sec_edgar.db`.
        limit: Maximum number of filings to yield. None processes all.
    """
    sql = (
        "SELECT accession_number, company_name, form_type, date_filed, body "
        "FROM filings "
        "WHERE status = 'fetched' AND body IS NOT NULL AND body != '' "
        "ORDER BY date_filed DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        body: str = row["body"]
        if not body.strip():
            continue
        company = row["company_name"] or row["accession_number"]
        title = f"{company} {row['form_type']} {row['date_filed']}".strip()
        version = content_hash(body)
        yield Doc(
            doc_id=row["accession_number"],
            title=title,
            version=f"{version}-{CLEANER_VERSION}",
            text=strip_html(body),
            section=None,
        )
