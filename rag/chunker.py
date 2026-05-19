"""Recursive boundary-aware chunker for the RAG pipeline.

Two chunkers ship here:

- `chunk_doc`: pure text → chunks, all tagged with `doc.section`. Default for
  sources whose content is naturally one block (arxiv title+abstract, openalex
  abstracts, gutenberg book bodies).
- `chunk_markdown`: split on `##`/`###`/`####` headings first (heading text
  goes into `section`, not the chunk body), then chunk each section. Default
  for sources whose content is structured (factbook, future arxiv full HTML).

Splitting uses `langchain-text-splitters.RecursiveCharacterTextSplitter` with
a paragraph→line→sentence→clause→word→char separator hierarchy, so chunks
end at the strongest available boundary. A hard-cap post-pass re-splits any
chunk that exceeded `max_chunk_size` after the soft-target split, preferring
word boundaries before falling through to a character cut.

Both functions return the same dict shape — `run_indexer` accepts either via
`chunk_fn`.
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
    """Recursive boundary split with a post-pass that enforces `max_chunk_size`.

    First pass uses the natural-boundary separator list. Any output chunk that
    still exceeds the hard cap (e.g. a single paragraph with no internal
    punctuation) is re-split with a tighter `[" ", ""]` splitter — that
    prefers word boundaries over arbitrary character cuts.
    """
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
    """Split a Doc's text into chunks, all tagged with `doc.section`.

    Args:
        doc: Source Doc. `doc.section` (when set) is applied to every output
            chunk. Extractors that want per-chunk sections should use
            `chunk_markdown` instead.
        chunk_size: Soft-target chunk length in characters. Most chunks land
            at or below this.
        overlap: Inter-chunk overlap in characters. Set to 0 in the current
            extractors; non-zero raises duplicate-token mass in embeddings.
        max_chunk_size: Hard cap; any chunk longer than this after the first
            split gets re-split with a word-boundary-preferring splitter.
            Defaults to ~1.2 × `chunk_size` when None.

    Returns:
        List of dicts with `section` (str|None), `chunk_index` (int, 0-based),
        `text` (str), `text_length` (int). Empty list if doc text is empty.
    """
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
    """Split markdown text by `##`/`###`/`####` headings, then chunk each section.

    Heading text is captured into the `section` field (and stripped from the
    chunk body so `## Geography` never reaches the embedder). Lead text before
    the first heading inherits `doc.section`. Overlap stays within a section —
    overlapping across section boundaries would mix Geography text into Economy
    chunks.

    Args:
        doc: Source Doc. `doc.text` is treated as markdown with ATX-style
            headings; lead content before the first heading gets `doc.section`.
        chunk_size: Soft target in characters.
        overlap: Inter-chunk overlap (within section).
        max_chunk_size: Hard cap; defaults to ~1.2 × `chunk_size`.

    Returns:
        List of dicts with `section` (str|None), `chunk_index` (int, 0-based
        within section), `text` (str), `text_length` (int). Empty list if
        every section ends up empty.
    """
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
        section_index = 0
        for part in parts:
            cleaned = normalize_whitespace(part)
            if not cleaned:
                continue
            chunks.append(
                {
                    "section": section,
                    "chunk_index": section_index,
                    "text": cleaned,
                    "text_length": len(cleaned),
                }
            )
            section_index += 1
    return chunks
