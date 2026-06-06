"""eCFR Doc-builder: render one `regulations` row into a Doc for RAG indexing.

Shared by the batch indexer (`scripts/ecfr/ecfr_rag_extract.py`) and the
API's live-embed route (`api.routers.ecfr.embed_regulation`). Lives in
`rag/` so both callers can import it — same reasoning as `rag.federal_register`.

Each `regulations` row yields flat prose: the section's `content` field as-is.
There are no `##` headings in eCFR content, so `chunk_doc` (not `chunk_markdown`)
is used. The DENSE profile (1000/1200/100) suits the short regulatory paragraphs.

Version key is `content_hash(heading, content)` plus `CLEANER_VERSION`.
"""

import sqlite3

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `regulations` row into a Doc.

    Returns None when the row has no body text. Columns expected on `row`:
    `id, title_num, section, heading, content`.
    """
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
