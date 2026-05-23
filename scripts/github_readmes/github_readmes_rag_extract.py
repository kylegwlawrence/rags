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

Link-dump filter: READMEs ≥10 KB with ≥8 markdown link markers per 1 KB are
skipped. Empirically these are curated awesome-style link lists (e.g.
avelino/awesome-go with ~8.6 links/KB across 389 KB) that chunk into hundreds
of low-signal "list of N links" passages — they dilute the retrieval pool
without adding embedding signal. The size floor avoids penalising small
badge-heavy or shortcut-heavy READMEs that may still contain real prose.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, strip_html

# Tuning knobs for the link-dump filter. See module docstring for rationale.
_LINK_FILTER_MIN_BYTES = 10_000
_LINK_FILTER_MAX_PER_KB = 8


def _is_link_dump(readme: str) -> bool:
    """Return True for large READMEs whose body is mostly markdown link markers.

    Counts occurrences of ``](`` — the boundary between a markdown link's
    visible text and its URL — as a cheap proxy for link density. Image
    markers (``![…](…)``) and in-page anchors (``](#…)``) are intentionally
    included in the count: awesome-style lists that pair each entry with an
    icon image are still link-dumps, and excluding images let canonical
    awesome lists (e.g. avelino/awesome-go, 0pandadev/awesome-windows) slip
    under the threshold during empirical tuning.

    READMEs under ``_LINK_FILTER_MIN_BYTES`` are exempt: a small README can be
    badge-heavy yet still contain useful prose worth embedding.
    """
    n = len(readme)
    if n < _LINK_FILTER_MIN_BYTES:
        return False
    link_count = readme.count("](")
    # links_per_kb >= threshold  <=>  link_count * 1000 >= threshold * n
    return link_count * 1000 >= _LINK_FILTER_MAX_PER_KB * n


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per fetched README row, skipping link-dump outliers.

    Args:
        conn: Read-only connection to `data/github/readmes.db`.
        limit: Maximum number of READMEs to *scan* (filtered docs still
            count against the limit). None processes all rows.
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
        if _is_link_dump(readme):
            continue
        version = content_hash(readme)
        yield Doc(
            doc_id=row["repo"],
            title=row["name"] or row["repo"],
            version=f"{version}-{CLEANER_VERSION}",
            text=strip_html(readme),
            section=None,
        )
