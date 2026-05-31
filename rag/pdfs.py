"""PDF Doc-builder + page-aware chunker, shared by the indexer and the API.

`pdfs.db` stores body text one row per *page* (`pages.text`), unlike every
other source whose body is a single blob. PDFs are also the only source whose
viewer renders the original file and can deep-link to a page (browsers honour a
`#page=N` URL fragment on a PDF). To make a semantic-search hit jump the viewer
to the right page, each chunk has to remember which page it came from.

The chunk schema only carries one per-chunk provenance field — `section` — so
the page number rides there as `"p. {n}"`. That string also lands in the
embedding header (`format_document` builds `"{title} - p. {n}"`); a low-value
page token is a deliberate, minor cost for keeping the page number visible to
the API and frontend.

Two pieces:

- `build_doc` — render one PDF into a `Doc`. Its `text` is every page joined by
  a form-feed sentinel so `chunk_pdf` can split the body back into pages while
  the rest of the pipeline (version-skip, batch embed, FTS rebuild) still sees a
  single document per PDF — matching how the FTS route already rolls page hits
  up to whole documents.
- `chunk_pdf` — the `chunk_fn` passed to `run_indexer` (batch) and `embed_doc`
  (the live embed button). Splits on the sentinel and chunks each page
  independently via the shared `chunk_doc`, tagging every chunk with its page.
  One chunk never spans two pages, so a hit maps to exactly one page (a sentence
  crossing a page break is split — an accepted trade for clean provenance).

Lives in `rag/` rather than `scripts/pdfs/` because both the indexer
(`scripts/pdfs/pdfs_rag_extract.py`) and the API's live-embed route
(`api.routers.pdfs.embed_document`) import it — same reasoning as
`rag.eurlex` / `rag.sec_filing`.
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
    """Render one PDF into a Doc, or None when it's unknown or has no text.

    The body is every page's text in page order, joined by `PAGE_SENTINEL`.
    Empty pages are kept as empty segments so segment index N lines up with
    `page_no` N when `chunk_pdf` splits the body back apart. Any stray sentinel
    inside a page's own text is replaced first so it can't shift that alignment.

    Args:
        conn: Read-only connection to `data/pdfs/pdfs.db`.
        doc_id: The PDF's filename stem (its `documents.doc_id`).

    Returns:
        A `Doc` (one per PDF), or None if the doc_id is unknown or the PDF has no
        extractable text (e.g. a scanned/image-only file).
    """
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
    """Split a PDF Doc into chunks, each tagged with its source page.

    Re-splits `doc.text` on `PAGE_SENTINEL`, then runs the shared boundary-aware
    `chunk_doc` over each page on its own (with `section="p. {n}"`) so every
    chunk stays within a single page. `chunk_index` is renumbered to run across
    the whole document in reading order, like the other chunkers.

    Same signature `run_indexer` and `embed_doc` call every `chunk_fn` with, so
    it drops straight into either.
    """
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
