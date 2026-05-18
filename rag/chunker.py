"""Paragraph-aware text chunker for the RAG pipeline.

Pure text in, list of chunk dicts out. No format-specific logic
(markdown, wikitext, HTML, etc.) — extractors do that work before calling here.
"""

from rag import Doc


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
    """Split a Doc's text into chunks.

    Args:
        doc: Source Doc. `doc.section` (when set) is applied to every output
            chunk. Extractors that want per-chunk sections should call
            `split_text` directly and build their own chunk dicts.
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
