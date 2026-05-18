"""Paragraph-aware text chunker for the RAG pipeline.

Two chunkers ship here:
- `chunk_doc`: pure text → chunks, all tagged with `doc.section`. Default for
  sources whose content is naturally one block (arxiv title+abstract, openalex
  abstracts).
- `chunk_markdown`: split on `##` … `######` headings first, then chunk each
  section. Each output chunk carries its section heading; lead text before
  the first heading inherits `doc.section`. Default for sources whose content
  is structured (factbook, future arxiv full HTML).

Both yield the same dict shape — `run_indexer` accepts either via `chunk_fn`.
"""

import re

from rag import Doc

_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)


def split_text(text: str, chunk_size: int, overlap: int = 0) -> list[str]:
    """Split text into parts of at most `chunk_size` chars at paragraph boundaries.

    Paragraph boundaries (double newlines) are preferred split points. A single
    paragraph longer than `chunk_size` is hard-split at character boundaries.

    `overlap > 0` prepends the last `overlap` chars of the previous part to the
    next part — improves recall on retrieval at the cost of duplicated tokens
    in adjacent vectors. Default 0 (no overlap) matches the existing arxiv_rag.db.

    Args:
        text: Plain text to split.
        chunk_size: Maximum character length of each returned part.
        overlap: Number of characters to repeat between adjacent parts.

    Returns:
        List of text parts. Empty list if `text` is empty/whitespace-only.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    parts: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip() if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                parts.append(current)
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size):
                    parts.append(para[i : i + chunk_size])
                current = ""
            else:
                current = para
    if current:
        parts.append(current)

    if overlap > 0 and overlap < chunk_size:
        overlapped = [parts[0]]
        for i in range(1, len(parts)):
            tail = parts[i - 1][-overlap:]
            overlapped.append(tail + parts[i])
        parts = overlapped

    return parts


def chunk_doc(doc: Doc, *, chunk_size: int = 1600, overlap: int = 0) -> list[dict]:
    """Split a Doc's text into chunks, all tagged with `doc.section`.

    Args:
        doc: Source Doc. `doc.section` (when set) is applied to every output
            chunk. Extractors that want per-chunk sections should use
            `chunk_markdown` instead.
        chunk_size: Maximum character length per chunk.
        overlap: Inter-chunk overlap in characters.

    Returns:
        List of dicts with `section` (str|None), `chunk_index` (int, 0-based),
        `text` (str), `text_length` (int). Empty list if doc text is empty.
    """
    parts = split_text(doc.text, chunk_size, overlap)
    return [
        {
            "section": doc.section,
            "chunk_index": i,
            "text": part,
            "text_length": len(part),
        }
        for i, part in enumerate(parts)
    ]


def chunk_markdown(doc: Doc, *, chunk_size: int = 1600, overlap: int = 0) -> list[dict]:
    """Split a Doc's markdown text on `##`…`######` headings, then chunk each section.

    Lead text before the first heading inherits `doc.section`. Each heading
    becomes its own chunk-section. Long sections are further split via
    `split_text`. `chunk_index` is 0-based within each section.

    Args:
        doc: Source Doc. `doc.text` is treated as markdown with ATX-style
            headings; lead content before the first heading gets `doc.section`.
        chunk_size: Maximum character length per chunk.
        overlap: Inter-chunk overlap in characters.

    Returns:
        List of dicts with `section` (str|None), `chunk_index` (int, 0-based
        within section), `text` (str), `text_length` (int). Empty list if all
        sections are empty.
    """
    md = doc.text
    if not md.strip():
        return []

    matches = list(_HEADING_RE.finditer(md))

    sections: list[tuple[str | None, str]] = []
    if not matches:
        sections.append((doc.section, md))
    else:
        lead = md[: matches[0].start()].strip()
        if lead:
            sections.append((doc.section, lead))
        for i, m in enumerate(matches):
            name = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
            sections.append((name, md[start:end].strip()))

    chunks: list[dict] = []
    for section, text in sections:
        if not text:
            continue
        for idx, part in enumerate(split_text(text, chunk_size, overlap)):
            if part.strip():
                chunks.append(
                    {
                        "section": section,
                        "chunk_index": idx,
                        "text": part,
                        "text_length": len(part),
                    }
                )
    return chunks
