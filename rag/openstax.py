r"""OpenStax CNXML/COLLXML parsing + Doc-builder, shared by script and API.

OpenStax ships each textbook as a GitHub `osbooks-*` repo of XML:

* `collections/<slug>.collection.xml` — **COLLXML**: the table of contents.
  A `<col:collection>` holds book metadata and a tree of `<col:subcollection>`
  chapters, each listing its sections as `<col:module document="mNNNNN"/>`
  references in reading order.
* `modules/<mNNNNN>/index.cnxml` — **CNXML**: one section. A `<title>`, learning
  objectives in `<metadata><md:abstract>`, and body prose in `<content>`
  (`<para>`, `<section>`, `<equation>`, tables, exercises, and presentation
  `<m:math>` for every formula).

This module turns that XML into plain rows + a `Doc`:

* `parse_collection` — COLLXML → book metadata + ordered (chapter, [module_id]).
* `cnxml_to_markdown` — one module's CNXML → (title, objectives, body markdown),
  with every `<m:math>` rebuilt as inline `\(…\)` LaTeX via `rag.mathml`.
* `build_doc` — a stored `sections` row → a `Doc` ready to chunk/embed.

Parsing uses the stdlib `xml.etree` (CNXML is well-formed XML), so the only new
dependency is none. Lives in `rag/` rather than `scripts/openstax/` because the
downloader imports the parsers while the indexer and the API's live-embed route
import `build_doc` — the same split as `rag.eurlex` / `rag.pdfs`.
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
    """One chapter (or front/back matter) from a collection's TOC.

    `number` is the 1-based chapter ordinal, or None for loose modules that
    sit outside any chapter (the preface, answer keys, etc.). `module_ids`
    lists the chapter's section modules in reading order.
    """

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
    """Parse a `.collection.xml` (COLLXML) string into a `CollectionInfo`.

    Top-level `<col:content>` children are walked in order: a `<col:module>`
    is loose matter (chapter None); a `<col:subcollection>` is a numbered
    chapter whose sections are every module beneath it (handles the rare
    nested-unit layout too). Chapter numbers count only subcollections, so the
    preface doesn't shift the numbering.
    """
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
# Elements whose content we drop entirely (images carry no useful text here).
_SKIP_TAGS = {"media", "image", "label", "metadata"}

# Heading level for a top-level `<section>` in a module's body. The module's
# own title is the page heading (level 1, stored separately), so its first tier
# of sub-sections starts at `##`; nested sections deepen by one each level,
# clamped to `######` (Markdown's deepest).
_BASE_HEADING_LEVEL = 2
_MAX_HEADING_LEVEL = 6


def _render(elem: Element, level: int = _BASE_HEADING_LEVEL) -> str:
    """Recursively render a CNXML element to plain text with inline LaTeX.

    `level` is the Markdown heading depth to use for a `<section>` title at this
    point in the tree; it deepens by one for each nested section.
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
        # Display equation on its own line, delimited by `\[…\]`.
        inner = "".join(_render(c, level) for c in elem).strip()
        # _render of the inner <m:math> already wrapped it as \(…\); promote to
        # display by swapping the inline delimiters for display ones.
        if inner.startswith("\\(") and inner.endswith("\\)"):
            inner = inner[2:-2].strip()
        return f"\n\n\\[{inner}\\]\n\n" if inner else ""
    if tag in _SKIP_TAGS:
        return ""
    if tag == "newline":
        return "\n"
    if tag == "section":
        return _render_section(elem, level)
    if tag == "item":
        return "\n- " + _inline(elem, level).strip()
    if tag == "entry":
        return _inline(elem, level).strip() + " | "
    if tag == "row":
        return "\n" + "".join(_render(c, level) for c in elem).rstrip(" |")

    text = _inline(elem, level)
    if tag in _BLOCK_TAGS:
        return "\n\n" + text.strip() + "\n\n"
    return text


def _render_section(elem: Element, level: int) -> str:
    """Render a `<section>`: its `<title>` as a `#`-heading, then its body.

    The heading uses `min(level, 6)` hashes; the section's other children render
    one level deeper so nested sub-sections nest their headings too.
    """
    parts: list[str] = []
    title_el = _find_local(elem, "title")
    if title_el is not None:
        heading = _inline(title_el, level).strip()
        if heading:
            hashes = "#" * min(level, _MAX_HEADING_LEVEL)
            parts.append(f"\n\n{hashes} {heading}\n\n")
    for child in elem:
        if child is title_el:  # already emitted as the heading above
            continue
        parts.append(_render(child, level + 1))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _inline(elem: Element, level: int = _BASE_HEADING_LEVEL) -> str:
    """Concatenate an element's text, rendered children, and their tails."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_render(child, level))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _extract_objectives(metadata: Element) -> str | None:
    """Pull learning objectives from a module's `<md:abstract>`, if present.

    The abstract is typically a lead "In this section, you will:" paragraph
    followed by a `<list>` of `<item>` objectives. Returns one objective per
    line, or None when the module has no abstract / no list items.
    """
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


def cnxml_to_markdown(xml_text: str) -> ParsedModule:
    r"""Parse one module's CNXML into title, objectives, and body markdown.

    Every `<m:math>` formula is rebuilt as inline `\(…\)` LaTeX (display
    `<equation>` as `\[…\]`); images are dropped; tables flatten to
    `cell | cell` rows. Whitespace is normalised so the chunker's paragraph
    boundaries line up.
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
        body = normalize_whitespace(_inline(content))

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
    """Render one `sections` row (joined with its book title) into a `Doc`.

    Expected columns on `row`: `section_id, book_title, chapter_title, title,
    objectives, body`. The doc text leads with the section title and its
    learning objectives (the outline signal) so a chunk matches a topic query
    even when the prose buries it, then the body. Returns None when the section
    has no body text.

    `Doc.section` is the human "Chapter — Section" label, which rides onto every
    chunk and into the embedding header. Pairs with `rag.chunker.chunk_doc`
    (flat prose); each module is already one logical section.
    """
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
