"""Federal Register Doc-builder. Shared by the batch indexer and the API's live-embed route.
Renders rows as ## Details / Abstract / Action / Excerpts markdown for chunk_markdown splitting.
"""

import sqlite3

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html


def _clean(value: str | None) -> str:
    """Strip HTML and normalise whitespace; return empty string for None/blank."""
    return normalize_whitespace(strip_html(value or ""))


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `documents` row into a markdown Doc; None when no usable text."""
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
