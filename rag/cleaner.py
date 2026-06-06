"""Text-cleanup helpers for RAG extractors.

`CLEANER_VERSION` is appended to Doc.version — bump it when any function here changes
so existing chunks are re-embedded on the next indexer run.
"""

import html
import re

from bs4 import BeautifulSoup

CLEANER_VERSION = "v3"


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities. Fast-paths when no `<` or `&` present."""
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
    """Collapse whitespace; preserve \\n\\n paragraph breaks; fold CRLF → \\n."""
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HSPACE_RUN_RE.sub(" ", text)
    text = _LEADING_HSPACE_RE.sub("\n", text)
    text = _TRAILING_HSPACE_RE.sub("\n", text)
    text = _BLANKLINE_RUN_RE.sub("\n\n", text)
    return text.strip()
