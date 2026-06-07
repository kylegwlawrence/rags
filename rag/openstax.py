r"""OpenStax CNXML/COLLXML parsing + Doc-builder. Shared by the downloader, indexer, and API route.

COLLXML (collections/<slug>.collection.xml) → parse_collection → book metadata + chapter ordering.
CNXML (modules/mNNNNN/index.cnxml) → cnxml_to_markdown → title + objectives + body with \(…\) LaTeX.
`build_doc` renders a stored `sections` row into a Doc ready to chunk/embed.
"""

import sqlite3
from dataclasses import dataclass
from xml.etree.ElementTree import Element, fromstring

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace
from rag.mathml import mathml_to_latex


def _local(tag: str) -> str:
    """Strip any `{namespace}` prefix, returning the bare element name."""
    return tag.rsplit("}", 1)[-1]


def _find_local(parent: Element, name: str) -> Element | None:
    """First direct child with the given local (namespace-stripped) name."""
    for child in parent:
        if _local(child.tag) == name:
            return child
    return None


# ---------------------------------------------------------------------------
# COLLXML — the table of contents
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Chapter:
    """One chapter from a COLLXML TOC. number=None for loose modules (preface, answer keys)."""

    number: int | None
    title: str | None
    module_ids: list[str]


@dataclass(frozen=True)
class CollectionInfo:
    """Parsed COLLXML: book metadata plus its ordered chapters."""

    title: str
    uuid: str | None
    slug: str | None
    license: str | None
    chapters: list[Chapter]


def _collect_modules(elem: Element) -> list[str]:
    """All `<col:module>` document ids under `elem`, in document order."""
    out: list[str] = []
    for node in elem.iter():
        if _local(node.tag) == "module":
            doc = node.get("document")
            if doc:
                out.append(doc)
    return out


def parse_collection(xml_text: str) -> CollectionInfo:
    """Parse a .collection.xml into CollectionInfo. Subcollections → numbered chapters; loose modules → None."""
    root = fromstring(xml_text)

    title = ""
    uuid = slug = license_text = None
    meta = _find_local(root, "metadata")
    if meta is not None:
        for child in meta:
            name = _local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "uuid":
                uuid = (child.text or "").strip()
            elif name == "slug":
                slug = (child.text or "").strip()
            elif name == "license":
                license_text = (child.text or "").strip() or child.get("url")

    chapters: list[Chapter] = []
    content = _find_local(root, "content")
    if content is not None:
        chapter_no = 0
        for child in content:
            name = _local(child.tag)
            if name == "module":
                doc = child.get("document")
                if doc:
                    chapters.append(Chapter(None, None, [doc]))
            elif name == "subcollection":
                chapter_no += 1
                sub_meta = _find_local(child, "metadata")
                ch_title = None
                title_el = _find_local(child, "title")
                if title_el is not None:
                    ch_title = (title_el.text or "").strip()
                elif sub_meta is not None:
                    mt = _find_local(sub_meta, "title")
                    ch_title = (mt.text or "").strip() if mt is not None else None
                module_ids = _collect_modules(child)
                if module_ids:
                    chapters.append(Chapter(chapter_no, ch_title, module_ids))

    return CollectionInfo(
        title=title, uuid=uuid, slug=slug, license=license_text, chapters=chapters
    )


# ---------------------------------------------------------------------------
# CNXML — one section's content
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedModule:
    """A rendered CNXML module: its title, learning objectives, and body."""

    title: str
    objectives: str | None   # one objective per line, or None when absent
    body: str                # plain-text/markdown with inline `\(…\)` LaTeX


# Block-level CNXML elements that should be followed by a paragraph break.
_BLOCK_TAGS = {"para", "title", "note", "example", "exercise", "problem",
               "solution", "definition", "equation", "figure", "table"}
# Wrappers we descend into without emitting any markup of their own. (A
# `<section>` is handled explicitly in `_render` — its `<title>` becomes a
# Markdown heading — so it is deliberately absent here.)
_TRANSPARENT_TAGS = {"content", "document", "tgroup", "tbody",
                     "thead", "commentary", "labeled-item", "glossary"}
# Elements whose content we drop entirely. (<media>/<image> are handled
# explicitly in `_render` so figures become Markdown image links.)
_SKIP_TAGS = {"label", "metadata"}

# Heading level for a top-level `<section>` in a module's body. The module's
# own title is the page heading (level 1, stored separately), so its first tier
# of sub-sections starts at `##`; nested sections deepen by one each level,
# clamped to `######` (Markdown's deepest).
_BASE_HEADING_LEVEL = 2
_MAX_HEADING_LEVEL = 6


def _render(elem: Element, level: int = _BASE_HEADING_LEVEL,
            media_prefix: str | None = None) -> str:
    """Recursively render a CNXML element to plain text with inline LaTeX.

    `level` is the Markdown heading depth to use for a `<section>` title at this
    point in the tree; it deepens by one for each nested section. `media_prefix`
    is the URL prefix for image links (e.g. `/openstax/media/{repo}`); None
    drops the prefix and keeps just the filename.
    """
    tag = _local(elem.tag)

    if tag == "math":
        latex = mathml_to_latex(elem)
        # Inline math uses LaTeX `\(…\)` delimiters rather than `$…$`: these
        # never occur in ordinary prose, so the viewer can detect math without
        # mistaking literal dollar amounts (common in stats/algebra word
        # problems) for formulas.
        return f" \\({latex}\\) " if latex else ""
    if tag == "equation":
        # An <equation> usually holds a single <m:math>, but can also carry
        # prose (a lead-in like "The standard error is:"). Render each <m:math>
        # as a display block `\[…\]` and anything else as ordinary text *outside*
        # the math delimiters — otherwise the prose lands inside math mode and
        # the whole formula fails to render.
        parts: list[str] = []
        if elem.text and elem.text.strip():
            parts.append(elem.text.strip())
        for child in elem:
            if _local(child.tag) == "math":
                latex = mathml_to_latex(child)
                if latex:
                    parts.append(f"\n\n\\[{latex}\\]\n\n")
            else:
                parts.append(_render(child, level, media_prefix))
            if child.tail and child.tail.strip():
                parts.append(child.tail)
        return "".join(parts)
    if tag == "media":
        return _render_media(elem, media_prefix)
    if tag in _SKIP_TAGS:
        return ""
    if tag == "newline":
        return "\n"
    if tag == "section":
        return _render_section(elem, level, media_prefix)
    if tag == "item":
        return "\n- " + _inline(elem, level, media_prefix).strip()
    if tag == "entry":
        return _inline(elem, level, media_prefix).strip() + " | "
    if tag == "row":
        return "\n" + "".join(
            _render(c, level, media_prefix) for c in elem).rstrip(" |")

    text = _inline(elem, level, media_prefix)
    if tag in _BLOCK_TAGS:
        return "\n\n" + text.strip() + "\n\n"
    return text


def _render_media(elem: Element, media_prefix: str | None) -> str:
    """Render a <media> wrapper as a Markdown image; '' for non-image media.

    The image `src` is a repo-relative path (`../../media/FILE.jpg`); we keep
    only the filename and prefix it with the served media URL. The `<media>`
    element's `alt` becomes the alt text, with brackets stripped so the
    `![alt](url)` syntax can't break.
    """
    image = _find_local(elem, "image")
    if image is None:
        return ""  # non-image media (video/audio/iframe) — nothing to render
    src = (image.get("src") or "").strip()
    if not src:
        return ""
    filename = src.rsplit("/", 1)[-1]
    url = f"{media_prefix}/{filename}" if media_prefix else filename
    alt = normalize_whitespace(elem.get("alt") or "")
    alt = alt.replace("[", "").replace("]", "").strip()
    return f"\n\n![{alt}]({url})\n\n"


def _render_section(elem: Element, level: int,
                    media_prefix: str | None = None) -> str:
    """Render a <section>: title as a #-heading (clamped to h6), children one level deeper."""
    parts: list[str] = []
    title_el = _find_local(elem, "title")
    if title_el is not None:
        heading = _inline(title_el, level, media_prefix).strip()
        if heading:
            hashes = "#" * min(level, _MAX_HEADING_LEVEL)
            parts.append(f"\n\n{hashes} {heading}\n\n")
    for child in elem:
        if child is title_el:  # already emitted as the heading above
            continue
        parts.append(_render(child, level + 1, media_prefix))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _inline(elem: Element, level: int = _BASE_HEADING_LEVEL,
            media_prefix: str | None = None) -> str:
    """Concatenate an element's text, rendered children, and their tails."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_render(child, level, media_prefix))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _extract_objectives(metadata: Element) -> str | None:
    """Extract list items from <md:abstract>; returns one per line, or None if absent."""
    abstract = _find_local(metadata, "abstract")
    if abstract is None:
        return None
    items: list[str] = []
    for node in abstract.iter():
        if _local(node.tag) == "item":
            text = normalize_whitespace(_inline(node)).strip()
            if text:
                items.append(text)
    if items:
        return "\n".join(items)
    # No list — fall back to the abstract's prose, if any.
    text = normalize_whitespace(_inline(abstract)).strip()
    return text or None


def cnxml_to_markdown(xml_text: str, media_prefix: str | None = None) -> ParsedModule:
    r"""Parse one module's CNXML → (title, objectives, body). Math → \(…\)/\[…\] LaTeX.

    `media_prefix` is the URL prefix for image links (e.g.
    `/openstax/media/osbooks-astronomy`); None keeps just the filename.
    """
    root = fromstring(xml_text)

    title = ""
    title_el = _find_local(root, "title")
    if title_el is not None:
        title = normalize_whitespace(_inline(title_el)).strip()

    objectives = None
    meta = _find_local(root, "metadata")
    if meta is not None:
        objectives = _extract_objectives(meta)
        if not title:  # fall back to the metadata title
            mt = _find_local(meta, "title")
            if mt is not None:
                title = (mt.text or "").strip()

    body = ""
    content = _find_local(root, "content")
    if content is not None:
        body = normalize_whitespace(_inline(content, media_prefix=media_prefix))

    return ParsedModule(title=title, objectives=objectives, body=body)


# ---------------------------------------------------------------------------
# Doc builder — shared by the RAG indexer and the API's live-embed route
# ---------------------------------------------------------------------------

def section_label(chapter_title: str | None, section_title: str) -> str:
    """Build the per-chunk `section` label: "Chapter — Section" (or just one)."""
    chapter_title = (chapter_title or "").strip()
    section_title = (section_title or "").strip()
    if chapter_title and section_title:
        return f"{chapter_title} — {section_title}"
    return section_title or chapter_title


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `sections` row into a Doc (title + objectives + body); None when body is empty."""
    body = (row["body"] or "").strip()
    if not body:
        return None

    objectives = (row["objectives"] or "").strip()
    title = (row["title"] or "").strip()

    parts = []
    if title:
        parts.append(title)
    if objectives:
        parts.append("Learning objectives:\n" + objectives)
    parts.append(body)
    text = "\n\n".join(parts)

    return Doc(
        doc_id=row["section_id"],
        title=(row["book_title"] or "").strip() or row["section_id"],
        version=f"{content_hash(title, objectives, body)}-{CLEANER_VERSION}",
        text=text,
        section=section_label(row["chapter_title"], title),
    )
