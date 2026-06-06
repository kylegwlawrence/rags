"""Boundary-aware chunkers for the RAG pipeline.

`chunk_doc`: flat prose → chunks tagged with doc.section.
`chunk_markdown`: split on ##/###/#### headings first, chunk each section independently.
Both use RecursiveCharacterTextSplitter (paragraph→word→char) with a hard-cap post-pass.
"""

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag import Doc
from rag.cleaner import normalize_whitespace

# Boundary hierarchy: try paragraph, then line, then end-of-sentence
# punctuation, then clause-level punctuation, then whitespace, then bare
# character. langchain consumes the first separator that produces chunks all
# fitting under `chunk_size`; the last empty string is the unconditional fallback.
_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]

_MD_HEADERS = [("##", "h2"), ("###", "h3"), ("####", "h4")]


def _resolve_max(chunk_size: int, max_chunk_size: int | None) -> int:
    """Default `max_chunk_size` to ~20% above the soft target if unset."""
    if max_chunk_size is None:
        return int(chunk_size * 1.2)
    return max_chunk_size


def _split_with_hard_cap(text: str, chunk_size: int, overlap: int, max_chunk_size: int) -> list[str]:
    """Split on natural boundaries; re-split oversized chunks with word-boundary fallback."""
    if not text:
        return []
    splitter = RecursiveCharacterTextSplitter(
        separators=_SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        length_function=len,
        is_separator_regex=False,
    )
    parts = splitter.split_text(text)

    cap = max(max_chunk_size, chunk_size)
    if all(len(p) <= cap for p in parts):
        return parts

    hard_splitter = RecursiveCharacterTextSplitter(
        separators=[" ", ""],
        chunk_size=cap,
        chunk_overlap=0,
        length_function=len,
        is_separator_regex=False,
    )
    capped: list[str] = []
    for part in parts:
        if len(part) <= cap:
            capped.append(part)
        else:
            capped.extend(hard_splitter.split_text(part))
    return capped


def chunk_doc(
    doc: Doc,
    *,
    chunk_size: int = 1500,
    overlap: int = 0,
    max_chunk_size: int | None = None,
) -> list[dict]:
    """Split a Doc's text into chunks, all tagged with doc.section."""
    text = normalize_whitespace(doc.text or "")
    if not text:
        return []
    cap = _resolve_max(chunk_size, max_chunk_size)
    parts = _split_with_hard_cap(text, chunk_size, overlap, cap)
    out: list[dict] = []
    for part in parts:
        cleaned = normalize_whitespace(part)
        if not cleaned:
            continue
        out.append(
            {
                "section": doc.section,
                "chunk_index": len(out),
                "text": cleaned,
                "text_length": len(cleaned),
            }
        )
    return out


def chunk_markdown(
    doc: Doc,
    *,
    chunk_size: int = 1000,
    overlap: int = 0,
    max_chunk_size: int | None = None,
) -> list[dict]:
    """Split on ##/###/#### headings → section field; overlap stays within a section."""
    text = normalize_whitespace(doc.text or "")
    if not text:
        return []
    cap = _resolve_max(chunk_size, max_chunk_size)

    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MD_HEADERS,
        strip_headers=True,
    )
    sections = md_splitter.split_text(text)

    chunks: list[dict] = []
    for section_doc in sections:
        meta = section_doc.metadata or {}
        section = meta.get("h2") or meta.get("h3") or meta.get("h4") or doc.section
        body = (section_doc.page_content or "").strip()
        if not body:
            continue
        parts = _split_with_hard_cap(body, chunk_size, overlap, cap)
        for part in parts:
            cleaned = normalize_whitespace(part)
            if not cleaned:
                continue
            chunks.append(
                {
                    "section": section,
                    # Document-wide running index (like chunk_doc), NOT a
                    # per-section counter. A per-section reset made the
                    # doc-chunks inspector interleave sections when it ordered
                    # by chunk_index; a global index keeps a document's chunks
                    # in reading order.
                    "chunk_index": len(chunks),
                    "text": cleaned,
                    "text_length": len(cleaned),
                }
            )
    return chunks
