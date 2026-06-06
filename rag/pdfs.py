"""PDF Doc-builder + page-aware chunker. Shared by the indexer and the API's live-embed route.

Pages are joined by a form-feed sentinel so chunk_pdf can split by page while the rest of the
pipeline sees one Doc per PDF. Each chunk's section is "p. N" for viewer deep-links (#page=N).
"""

import sqlite3

from rag import Doc, content_hash
from rag.chunker import chunk_doc
from rag.cleaner import CLEANER_VERSION

# Page boundary marker stitched between pages in a Doc's text. Form feed (U+000C)
# is the conventional page-break control character and pdfplumber's
# extract_text() never emits it, so it can't collide with real page content.
PAGE_SENTINEL = "\f"


def build_doc(conn: sqlite3.Connection, doc_id: str) -> Doc | None:
    """Render one PDF into a Doc (pages joined by sentinel); None if unknown or no text."""
    doc_row = conn.execute(
        "SELECT doc_id, title FROM documents WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if doc_row is None:
        return None

    page_rows = conn.execute(
        "SELECT text FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,)
    ).fetchall()
    # Defensive: strip any sentinel a page might somehow contain so the split in
    # chunk_pdf yields exactly one segment per page.
    pages = [(r["text"] or "").replace(PAGE_SENTINEL, " ") for r in page_rows]
    body = PAGE_SENTINEL.join(pages)
    if not body.strip():
        return None

    title = (doc_row["title"] or "").strip() or doc_id
    return Doc(
        doc_id=doc_id,
        title=title,
        version=f"{content_hash(body)}-{CLEANER_VERSION}",
        text=body,
        section=None,
    )


def chunk_pdf(
    doc: Doc,
    *,
    chunk_size: int = 1500,
    overlap: int = 0,
    max_chunk_size: int | None = None,
) -> list[dict]:
    """Chunk each page independently (section="p. N") so hits map to exactly one page."""
    pages = (doc.text or "").split(PAGE_SENTINEL)
    out: list[dict] = []
    for page_no, page_text in enumerate(pages, start=1):
        page_doc = Doc(
            doc_id=doc.doc_id,
            title=doc.title,
            version=doc.version,
            text=page_text,
            section=f"p. {page_no}",
        )
        for chunk in chunk_doc(
            page_doc,
            chunk_size=chunk_size,
            overlap=overlap,
            max_chunk_size=max_chunk_size,
        ):
            chunk["chunk_index"] = len(out)  # document-wide running index
            out.append(chunk)
    return out
