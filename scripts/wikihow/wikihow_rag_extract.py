"""Extract one Doc per wikiHow guide for the RAG indexer.

`wikihow.db` stores one row per step (`articles`), with several rows sharing a
guide `title`. This extractor reconstructs whole guides by grouping rows on
`title` and ordering by `id` (which preserves the CSV's step order), then
renders each guide as section-headered markdown shaped like:

    ## Overview
    <overview text>

    ## Using Home Remedies
    Soak the area in warm water.
    <step text>

    ## Getting Medical Treatment
    See a doctor if it persists.
    <step text>

The guide-level `overview` becomes a leading `## Overview` section; each
distinct `sectionLabel` becomes its own `##` heading (emitted once, even when
several consecutive steps share it). Within a section, each step contributes
its `headline` as a lead line followed by the step `text`. The `##` headings
are what `rag.chunker.chunk_markdown` splits on, so each chunk's `section`
column carries the real wikiHow section name.

Every leaf string is HTML-stripped and whitespace-normalised by `rag.cleaner`
(the source text carries stray `;`/`<br>`-style artifacts). Version key is
``content_hash(...) + "-" + CLEANER_VERSION`` — wikihow.db has no per-row
`updated_at`, so a content hash is the only edit-detection signal, and the
CLEANER_VERSION suffix invalidates every guide when cleaning behaviour changes.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html


def iter_docs(
    wikihow_conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per wikiHow guide, grouping `articles` rows by title.

    Args:
        wikihow_conn: Read-only connection to `data/wikihow/wikihow.db`.
        limit: Maximum number of guides to yield. None processes every guide.

    Steps are read in `(title, id)` order so a guide's rows stay contiguous
    and in CSV/step order regardless of how the table was populated.
    """
    cursor = wikihow_conn.execute(
        "SELECT title, section_label, headline, overview, text "
        "FROM articles ORDER BY title, id"
    )

    current_title: str | None = None
    rows: list[sqlite3.Row] = []
    emitted = 0

    for row in cursor:
        title = row["title"] or ""
        if current_title is None:
            current_title = title
        if title != current_title:
            doc = _build_doc(current_title, rows)
            if doc is not None:
                yield doc
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
            current_title = title
            rows = []
        rows.append(row)

    if rows and (limit is None or emitted < limit):
        doc = _build_doc(current_title or "", rows)
        if doc is not None:
            yield doc


def _build_doc(title: str, rows: list[sqlite3.Row]) -> Doc | None:
    """Render one guide's step rows into a markdown Doc, or None if it's empty."""
    overview = ""
    for row in rows:
        overview = normalize_whitespace(strip_html(row["overview"] or ""))
        if overview:
            break

    parts: list[str] = []
    if overview:
        parts.append(f"## Overview\n{overview}")

    current_section: str | None = None
    section_lines: list[str] = []

    def flush_section() -> None:
        if section_lines:
            heading = current_section or "Steps"
            parts.append(f"## {heading}\n" + "\n\n".join(section_lines))

    for row in rows:
        section = normalize_whitespace(strip_html(row["section_label"] or "")) or None
        headline = normalize_whitespace(strip_html(row["headline"] or ""))
        text = normalize_whitespace(strip_html(row["text"] or ""))
        body = "\n\n".join(p for p in (headline, text) if p)
        if not body:
            continue
        if section != current_section:
            flush_section()
            current_section = section
            section_lines = []
        section_lines.append(body)
    flush_section()

    rendered = "\n\n".join(parts)
    if not rendered.strip():
        return None

    version = content_hash(
        overview,
        *(
            f"{row['section_label']}\x1f{row['headline']}\x1f{row['text']}"
            for row in rows
        ),
    )
    return Doc(
        doc_id=title,
        title=title,
        version=f"{version}-{CLEANER_VERSION}",
        text=rendered,
        section=None,
    )
