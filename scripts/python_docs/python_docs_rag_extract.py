"""Extract one Doc per Python documentation page for the RAG indexer.

Reads `docs` rows from `data/pydocs/python_docs.db` and converts each page's
Sphinx text-builder output (underline-style headings, RST emphasis) into
markdown so `rag.chunker.chunk_markdown` can split on `##` / `###` / `####`
boundaries and tag every chunk with its real section name (e.g.
"Process Parameters", "Python UTF-8 Mode", "Built-in Constants").

Sphinx text-builder heading convention (verified across all 513 docs):

    *** = h1 page title (dropped — already in docs.title)
    === = h2 section          → ##
    --- = h3 subsection       → ###
    ~~~ = h4 subsubsection    → ####
    ^^^ = h5 paragraph        → #####
    \"\"\" = h6 sub-paragraph    → ######

A line is treated as an underline only when it sits directly under non-empty
text and its length matches the heading length (Sphinx pads underlines to
exactly the heading length; only ±3 chars slop is allowed). This rejects
horizontal-rule transitions like the 70-char `=====...===` separators in
whatsnew docs, which sit with a blank line above them.

The `+` character is *not* in the heading set — it's used for `+---+---+`
RST table borders, which would otherwise be mistaken for headings.

Version key is ``sha256(content)[:32]-CLEANER_VERSION``: the source DB has
no per-row updated_at, so we fall back to a content hash. The CLEANER_VERSION
suffix invalidates every doc when cleaning behaviour changes.
"""

import hashlib
import re
import sqlite3
from collections.abc import Iterator

from rag import Doc
from rag.cleaner import CLEANER_VERSION

# Sphinx text-builder uses these characters as heading underlines. `+` is
# excluded so `+---+---+` table borders are left alone. `*` maps to h1 (the
# page title), which the renderer drops because docs.title already carries it.
_HEADING_LEVELS = {
    "*": 1,
    "=": 2,
    "-": 3,
    "~": 4,
    "^": 5,
    '"': 6,
}

_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
# Italic content must start with a non-space char so RST bullet lists
# (`* item`) don't have their leading `*` swallowed. Allow up to ~80 chars
# of non-star content (incl. one wrapped line — Sphinx text-builder hard-wraps
# prose around col 70, so `*type\\ncheckers*` is a real case to cover) and a
# final `\\S` so trailing whitespace doesn't sneak in.
_ITALIC_RE = re.compile(r"(?<!\*)\*(\S(?:[^*]{0,80}?\S)?)\*(?!\*)")


def iter_docs(
    pydocs_conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> Iterator[Doc]:
    """Yield one Doc per row in `python_docs.docs`, ordered by `id`.

    Args:
        pydocs_conn: Read-only connection to `data/pydocs/python_docs.db`.
        limit: Maximum number of docs to yield. None processes the full set
            (~513 pages for a current 3.13 dump).
    """
    if limit is not None:
        cursor = pydocs_conn.execute(
            "SELECT doc_path, section, title, content "
            "FROM docs ORDER BY id LIMIT ?",
            (limit,),
        )
    else:
        cursor = pydocs_conn.execute(
            "SELECT doc_path, section, title, content FROM docs ORDER BY id"
        )
    for row in cursor:
        content = row["content"]
        if not content:
            continue
        markdown = sphinx_text_to_markdown(content)
        if not markdown.strip():
            continue
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
        yield Doc(
            doc_id=row["doc_path"],
            title=row["title"] or row["doc_path"],
            version=f"{digest}-{CLEANER_VERSION}",
            text=markdown,
            section=row["section"],
        )


def sphinx_text_to_markdown(text: str) -> str:
    """Convert Sphinx text-builder output into markdown for chunk_markdown.

    Headings: each `Heading\\nUnderline` pair becomes `## Heading` (or deeper,
    per `_HEADING_LEVELS`). The page title (h1) is dropped because the same
    text is already in `docs.title` and gets prepended by the embedder.

    Inline emphasis: `**bold**` and `*italic*` are unwrapped so the asterisks
    don't reach the embedder. Indented code blocks, function signatures, and
    `+---+---+` RST table borders are left untouched.

    Args:
        text: Raw content of a `docs.content` row.

    Returns:
        Markdown rendering suitable for `rag.chunker.chunk_markdown`.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        heading_level = _heading_level(line, next_line)
        if heading_level is not None:
            if heading_level > 1:
                out.append("#" * heading_level + " " + line.strip())
            # heading_level == 1 is the page title — drop entirely so the
            # embedder doesn't see it twice (docs.title is already prepended
            # by embedder.format_document).
            i += 2
            continue
        out.append(line)
        i += 1
    rendered = "\n".join(out)
    rendered = _BOLD_RE.sub(r"\1", rendered)
    rendered = _ITALIC_RE.sub(r"\1", rendered)
    return rendered


def _heading_level(line: str, next_line: str) -> int | None:
    """Return the markdown heading level if `next_line` underlines `line`, else None.

    Sphinx text-builder pads underlines to exactly the heading length. We
    allow up to +3 chars of slop but reject any case where the marker is
    much longer than the heading (those are horizontal-rule transitions, not
    headings — they sit with a blank line above them, hence `line.strip()`
    being empty also bails out here).
    """
    text = line.strip()
    if not text:
        return None
    marker = next_line.rstrip()
    if len(marker) < 3:
        return None
    char = marker[0]
    if char not in _HEADING_LEVELS:
        return None
    if any(c != char for c in marker):
        return None
    heading_len = len(text)
    marker_len = len(marker)
    if not (heading_len <= marker_len <= heading_len + 3):
        return None
    return _HEADING_LEVELS[char]
