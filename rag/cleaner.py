"""Text-cleanup helpers used by per-source RAG extractors.

`strip_html` removes HTML tags and decodes entities. `strip_markdown` removes
syntax noise (`**bold**`, `[text](url)`, `##` markers) while preserving the
heading text itself. `normalize_whitespace` collapses runs of horizontal
whitespace but keeps `\\n\\n` paragraph boundaries (the chunker splits on them).

`CLEANER_VERSION` is appended to every per-source `Doc.version` string so that
existing chunks get re-embedded on the next indexer run after any change to
the cleaning behaviour. Bump the version when changing any function here.

Per-source extractors call these directly (not the shared indexer) — each
source has its own idea of what counts as noise (factbook walks JSON leaves,
gutenberg has Project-Gutenberg banners, arxiv abstracts are mostly clean
prose), so cleanup decisions stay close to the source.
"""

import html
import re

from bs4 import BeautifulSoup

CLEANER_VERSION = "v1"


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities, returning plain text.

    Fast-path: returns input unchanged when no `<` and no `&` appear (avoids
    parser overhead on the ~70% of arxiv/openalex strings that are already
    clean prose). Otherwise parses with BeautifulSoup and decodes any bare
    entities (`&amp;`, `&lt;`) that survive without surrounding tags.
    """
    if not text:
        return text
    if "<" not in text and "&" not in text:
        return text
    parsed = BeautifulSoup(text, "html.parser").get_text(separator=" ")
    return html.unescape(parsed)


_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BOLD_STAR_RE = re.compile(r"\*\*([^*]+)\*\*")
_BOLD_UNDER_RE = re.compile(r"__([^_]+)__")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<!_)_([^_\n]+)_(?!_)")
_HEADING_MARKER_RE = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_HRULE_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Remove markdown syntax noise but keep the underlying text.

    Headings keep their text (the `## ` marker is stripped, "Geography"
    survives). Bold/italic unwrap to their content. Links collapse to the
    visible text. Fenced code blocks are dropped entirely.
    """
    if not text:
        return text
    text = _FENCED_CODE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _BOLD_STAR_RE.sub(r"\1", text)
    text = _BOLD_UNDER_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    text = _HEADING_MARKER_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _HRULE_RE.sub("", text)
    return text


_HSPACE_RUN_RE = re.compile(r"[ \t]+")
_LEADING_HSPACE_RE = re.compile(r"\n[ \t]+")
_TRAILING_HSPACE_RE = re.compile(r"[ \t]+\n")
_BLANKLINE_RUN_RE = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    """Collapse whitespace runs; preserve `\\n\\n` paragraph breaks; fold CRLF.

    Windows / Project-Gutenberg-style `\\r\\n` line endings are normalised to
    `\\n` so the chunker's paragraph separator (`\\n\\n`) actually matches —
    otherwise `\\r\\n\\r\\n` falls through to the single-newline separator and
    sentences get cut across line breaks. Three-plus newlines collapse to
    exactly two.
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HSPACE_RUN_RE.sub(" ", text)
    text = _LEADING_HSPACE_RE.sub("\n", text)
    text = _TRAILING_HSPACE_RE.sub("\n", text)
    text = _BLANKLINE_RUN_RE.sub("\n\n", text)
    return text.strip()
