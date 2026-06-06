"""EUR-Lex Doc-builder. Shared by the batch indexer and the API's live-embed route."""

import sqlite3

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `laws` row into a Doc; None when act_raw_text is empty."""
    body = row["act_raw_text"] or ""
    if not body.strip():
        return None
    celex = row["CELEX"]
    title = normalize_whitespace(row["Act_name"] or "") or celex
    return Doc(
        doc_id=celex,
        title=title,
        version=f"{content_hash(body)}-{CLEANER_VERSION}",
        text=body,
        section=None,
    )
