"""Convert MediaWiki wikitext to markdown.

The single public entry point is ``wikitext_to_markdown(wt) -> str``. The output
is fed to ``rag.chunker.chunk_markdown`` for section-aware chunking, so the goal
is *clean markdown that preserves the textual structure of the article* —
section headings, paragraphs, list items, link display text — and drops
chrome (templates, file/image references, references).

Wikitext structure cheatsheet:

* sections: ``== Title ==`` (h2) up to ``====== Title ======`` (h6); single
  ``=`` is reserved for the article title and never appears in body text
* paragraphs separated by blank lines
* wikilinks: ``[[Target]]`` or ``[[Target|Display]]``. ``[[File:foo.jpg|...]]``
  and ``[[Image:foo.jpg|...]]`` are figure references and get dropped entirely;
  ``[[Category:...]]`` is metadata and also dropped.
* external links: ``[http://url Display]`` or bare ``http://url``
* bold/italic: ``'''bold'''`` / ``''italic''``
* lists: ``* item`` or ``# item``; nesting via repeated ``*``/``#``
* templates: ``{{name|arg1|arg2}}`` — body varies; rendered chrome we drop
* tables: ``{| ... |}`` — per Phase 4 scope we skip; only prose chunks ship
* redirects: ``#REDIRECT [[Target]]`` at the very top — return "" so the
  extractor filters the page out.

The renderer is intentionally simpler than MediaWiki's: we don't expand
templates (the dump would need a separate per-template renderer), and we
don't render tables. Mwparserfromhell's ``strip_code()`` does most of the
heavy lifting at the section-fragment level; this module adds the section
header preservation that ``strip_code()`` alone discards.
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


def is_redirect(wikitext: str) -> bool:
    """Return True if this article is a #REDIRECT stub."""
    return bool(_REDIRECT_RE.match(wikitext or ""))


def redirect_target(wikitext: str) -> str | None:
    """Return the target article title of a ``#REDIRECT``, or None if not one.

    Drops any ``#section`` anchor and ``|display`` suffix, leaving the bare
    target title. Whitespace and underscores are left as-is for the caller to
    normalise against stored titles.
    """
    m = _REDIRECT_TARGET_RE.match(wikitext or "")
    if m is None:
        return None
    return m.group(1).strip()


def wikitext_to_markdown(wikitext: str) -> str:
    """Convert MediaWiki wikitext to markdown for the RAG chunker.

    Headings keep their level (``==`` → ``##``, ``===`` → ``###``, ...). Bodies
    are passed through ``mwparserfromhell.strip_code()`` to flatten templates,
    formatting, and references; file/image/category wikilinks are removed
    before stripping so their caption text doesn't survive into the chunk.

    Returns "" for redirect articles and articles with no extractable prose.
    """
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
    """Convert a wikitext fragment to plain text, dropping file/image/category links.

    File/image/category wikilinks are removed *before* ``strip_code()`` so
    their pipe-separated caption text (often long alt-text or thumbnails)
    doesn't survive as orphaned prose.
    """
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
