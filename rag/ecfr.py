"""eCFR Doc-builder. Shared by the batch indexer and the API's live-embed route."""

import sqlite3

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `regulations` row into a Doc; None when content is empty."""
    reg_id = row["id"]
    heading = (row["heading"] or "").strip()
    content = (row["content"] or "").strip()

    if not content:
        return None

    title = heading or f"Title {row['title_num']} § {row['section']}"

    return Doc(
        doc_id=str(reg_id),
        title=title,
        version=f"{content_hash(heading, content)}-{CLEANER_VERSION}",
        text=content,
        section=None,
    )
