"""Convert MediaWiki wikitext to markdown for chunk_markdown section-aware chunking.

Wikitext structure: == heading == (h2) … ====== (h6); [[Target|Display]] wikilinks;
[[File:/Image:/Category:]] dropped; templates dropped via mwparserfromhell.strip_code();
#REDIRECT → "" (extractor filters). Tables are not rendered (only prose chunks ship).
"""

import re

import mwparserfromhell

_SECTION_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
_REDIRECT_RE = re.compile(r"^\s*#\s*REDIRECT\s*\[\[", re.IGNORECASE)
# Captures the target title only: stops at the section anchor (#), the
# display-text pipe (|), or the closing brackets (]).
_REDIRECT_TARGET_RE = re.compile(r"^\s*#\s*REDIRECT\s*\[\[\s*([^\]|#]+)", re.IGNORECASE)
_FILE_PREFIXES = ("file:", "image:", "media:")
_CATEGORY_PREFIX = "category:"
_BLANK_RUN_RE = re.compile(r"\n{3,}")

# Sections that are pure navigation/bibliographic noise — lists of links or
# titles with no prose value for retrieval.
_STRIP_TAGS = frozenset({"ref", "references", "gallery"})

_NAV_SECTIONS = frozenset({
    "related pages", "other websites", "see also", "external links",
    "references", "notes", "further reading", "bibliography",
    "footnotes", "citations",
})


_WHITESPACE_RUN_RE = re.compile(r"\s+")


def normalize_category(name: str) -> str:
    """Normalize to MediaWiki canonical form: underscores→spaces, first letter capitalised, stripped."""
    name = name.replace("_", " ")
    name = _WHITESPACE_RUN_RE.sub(" ", name).strip()
    if not name:
        return ""
    return name[0].upper() + name[1:]


def is_redirect(wikitext: str) -> bool:
    """Return True if this article is a #REDIRECT stub."""
    return bool(_REDIRECT_RE.match(wikitext or ""))


def redirect_target(wikitext: str) -> str | None:
    """Return the redirect target title (no #anchor or |display suffix), or None if not a redirect."""
    m = _REDIRECT_TARGET_RE.match(wikitext or "")
    if m is None:
        return None
    return m.group(1).strip()


def wikitext_to_markdown(wikitext: str) -> str:
    """Convert wikitext to markdown. == → ##; bodies stripped via mwparserfromhell. Returns "" for redirects."""
    if not wikitext:
        return ""
    if is_redirect(wikitext):
        return ""

    matches = list(_SECTION_RE.finditer(wikitext))
    parts: list[str] = []

    if not matches:
        body = _strip_fragment(wikitext)
        if body:
            parts.append(body)
    else:
        lead = wikitext[: matches[0].start()]
        lead_body = _strip_fragment(lead)
        if lead_body:
            parts.append(f"## Introduction\n\n{lead_body}")
        for i, m in enumerate(matches):
            level = len(m.group(1))
            heading = m.group(2).strip()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(wikitext)
            fragment = wikitext[m.end():end]
            body = _strip_fragment(fragment)
            if not heading and not body:
                continue
            if heading.lower() in _NAV_SECTIONS:
                continue
            # Map ==..====== (2..6 equals signs) onto markdown ##..######.
            # `chunk_markdown` splits on ##/###/####; deeper headings just
            # become extra noise inside a section, which is the right
            # behaviour for navigational subheaders like "References" >
            # "Notes" > "Citations".
            md_marker = "#" * level
            parts.append(f"{md_marker} {heading}\n\n{body}" if body else f"{md_marker} {heading}")

    text = "\n\n".join(parts)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def _strip_fragment(fragment: str) -> str:
    """Strip wikitext fragment to plain text. File/Category wikilinks removed before strip_code()."""
    if not fragment or not fragment.strip():
        return ""
    try:
        parsed = mwparserfromhell.parse(fragment)
    except (ValueError, AttributeError):
        # mwparserfromhell can raise on severely malformed input; fall back
        # to the raw fragment so we don't lose the section entirely.
        return fragment.strip()

    for tag in list(parsed.filter_tags(matches=lambda n: str(n.tag).lower() in _STRIP_TAGS)):
        try:
            parsed.remove(tag)
        except ValueError:
            pass

    for wl in list(parsed.filter_wikilinks()):
        title = str(wl.title).strip().lower()
        if title.startswith(_FILE_PREFIXES) or title.startswith(_CATEGORY_PREFIX):
            try:
                parsed.remove(wl)
            except ValueError:
                # Nested wikilink already removed when its parent went; ignore.
                pass

    try:
        stripped = parsed.strip_code()
    except (ValueError, AttributeError):
        stripped = fragment

    stripped = _BLANK_RUN_RE.sub("\n\n", stripped)
    return stripped.strip()
