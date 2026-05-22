"""Extract one Doc per Federal Register document for the RAG indexer.

Each `documents` row is rendered as section-headered markdown:

    ## Details
    Type: Rule
    Date: 2024-01-15
    Agencies: Environmental Protection Agency
    Effective date: 2024-03-01

    ## Abstract
    This rule establishes ...

    ## Action
    Final rule.

    ## Excerpts
    ...relevant passage...

Empty fields are omitted. `chunk_markdown` splits on `##` headings so each
chunk's `section` column carries "Details", "Abstract", "Action", or "Excerpts".

Version key is `content_hash(title, abstract, action, excerpts)` plus
`CLEANER_VERSION` — `federal_register.db` has no per-row `updated_at`, so a
content hash is the edit-detection signal.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html


def iter_docs(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per row in `federal_register.documents`.

    Args:
        conn: Read-only connection to `data/federal_register/federal_register.db`.
        limit: Maximum number of documents to yield. None processes all.
    """
    sql = (
        "SELECT document_number, title, abstract, type, publication_date, "
        "       agencies, action, effective_date, excerpts "
        "FROM documents ORDER BY publication_date DESC, document_number"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    for row in conn.execute(sql):
        doc = _build_doc(row)
        if doc is not None:
            yield doc


def _clean(value: str | None) -> str:
    """Strip HTML and normalise whitespace; return empty string for None/blank."""
    return normalize_whitespace(strip_html(value or ""))


def _build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one documents row into a markdown Doc, or None if it has no usable text."""
    doc_number = row["document_number"] or ""
    title = _clean(row["title"])
    abstract = _clean(row["abstract"])
    action = _clean(row["action"])
    excerpts = _clean(row["excerpts"])

    if not (title or abstract):
        return None

    parts: list[str] = []

    # Details block — collect non-empty key: value lines
    detail_lines: list[str] = []
    if row["type"]:
        detail_lines.append(f"Type: {_clean(row['type'])}")
    if row["publication_date"]:
        detail_lines.append(f"Date: {row['publication_date']}")
    if row["agencies"]:
        detail_lines.append(f"Agencies: {_clean(row['agencies'])}")
    if row["effective_date"]:
        detail_lines.append(f"Effective date: {row['effective_date']}")
    if detail_lines:
        parts.append("## Details\n" + "\n".join(detail_lines))

    if abstract:
        parts.append(f"## Abstract\n{abstract}")
    if action:
        parts.append(f"## Action\n{action}")
    if excerpts:
        parts.append(f"## Excerpts\n{excerpts}")

    text = "\n\n".join(parts)
    if not text.strip():
        return None

    version = content_hash(
        title, abstract, action, excerpts,
        row["type"], row["publication_date"], row["agencies"], row["effective_date"],
    )
    return Doc(
        doc_id=doc_number,
        title=title or doc_number,
        version=f"{version}-{CLEANER_VERSION}",
        text=text,
        section=None,
    )
