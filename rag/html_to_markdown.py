"""Convert arXiv LaTeXML HTML to markdown for chunk_markdown section-aware chunking.

LaTeXML structure: <article class="ltx_document">; sections <h2 class="ltx_title">;
abstract <h6 class="ltx_title_abstract"> (promoted to ##); math <math alttext="...">
(alttext is the original LaTeX); section numbers in <span class="ltx_tag"> stripped.
"""

from bs4 import BeautifulSoup, NavigableString, Tag

# CSS selectors for subtrees we drop entirely before walking. The TOC and
# navbar are chrome; the document title and authors are metadata we already
# have on the abstract page. `.ltx_tag` (section number prefixes) is NOT
# stripped here — figure / table captions use the same span (e.g. "Figure 1: ")
# and we want to keep those. Heading handlers strip their own tag prefixes.
_DROP_SELECTORS = (
    "nav",
    "header",
    "footer",
    "script",
    "style",
    ".ltx_TOC",
    ".ltx_page_navbar",
    ".ltx_dates",
    ".ltx_authors",
    ".ltx_title_document",
)


def html_to_markdown(html: str) -> str:
    """Convert arxiv-rendered HTML to markdown."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.ltx_document") or soup.body or soup

    for selector in _DROP_SELECTORS:
        for el in article.select(selector):
            el.decompose()

    parts: list[str] = []
    _render(article, parts)
    return _cleanup("".join(parts))


def _render(node: NavigableString | Tag, parts: list[str]) -> None:
    """Walk ``node`` recursively, appending markdown fragments to ``parts``."""
    if isinstance(node, NavigableString):
        parts.append(str(node))
        return
    if not isinstance(node, Tag):
        return

    name = node.name
    classes = set(node.get("class") or [])

    if name == "math":
        _emit_math(node, parts)
        return

    if name == "h1":
        return  # paper title — already shown above the article body

    if name in ("h2", "h3", "h4", "h5", "h6"):
        _emit_heading(node, name, classes, parts)
        return

    if name == "table":
        parts.append(_render_table(node))
        return

    if name == "figure":
        # arXiv often nests figures (an outer layout-only `<figure>` wraps a
        # `<div>` that contains the inner `<figure class="ltx_table">` with
        # the actual caption + content). Extract the first descendant caption
        # we find, emit it, then descend so any nested table/figure renders
        # via normal recursion. Drop `<img>` — local view re-renders the HTML.
        cap = node.find("figcaption")
        if cap is not None:
            parts.append(f"\n*{_inline_text(cap)}*\n\n")
            cap.extract()
        for child in node.children:
            if isinstance(child, Tag) and child.name == "img":
                continue
            _render(child, parts)
        return

    if name in ("ul", "ol"):
        _render_list(node, parts, ordered=(name == "ol"))
        return

    if name == "p":
        for child in node.children:
            _render(child, parts)
        parts.append("\n\n")
        return

    if name == "br":
        parts.append("\n")
        return

    if name == "cite":
        keys = [a.get_text(" ", strip=True) for a in node.find_all("a")]
        if keys:
            parts.append(f"[{', '.join(keys)}]")
        return

    if name == "a":
        href = node.get("href", "")
        text = _inline_text(node)
        if href and not href.startswith("#"):
            parts.append(f"[{text}]({href})")
        else:
            parts.append(text)
        return

    if name == "pre":
        parts.append(f"\n```\n{node.get_text('')}\n```\n\n")
        return

    if name == "code":
        parts.append(f"`{node.get_text('')}`")
        return

    if "ltx_font_bold" in classes:
        parts.append("**")
        for child in node.children:
            _render(child, parts)
        parts.append("**")
        return

    if "ltx_font_italic" in classes:
        parts.append("*")
        for child in node.children:
            _render(child, parts)
        parts.append("*")
        return

    if "ltx_font_typewriter" in classes:
        parts.append("`")
        for child in node.children:
            _render(child, parts)
        parts.append("`")
        return

    for child in node.children:
        _render(child, parts)


def _emit_math(node: Tag, parts: list[str]) -> None:
    """Emit a ``<math>`` element as ``$...$`` or ``$$...$$``."""
    alttext = node.get("alttext")
    if alttext is None:
        alttext = node.get_text("")
    delim = "$$" if node.get("display") == "block" else "$"
    parts.append(f"{delim}{alttext}{delim}")


def _emit_heading(node: Tag, name: str, classes: set[str], parts: list[str]) -> None:
    """Emit heading markdown. Abstract h6 → ## ; section numbers stripped here (not globally)."""
    for tag_span in node.select(".ltx_tag"):
        tag_span.decompose()
    text = _inline_text(node).strip()
    if not text:
        return
    if "ltx_title_abstract" in classes:
        level = 2
    else:
        level = int(name[1])
    parts.append(f"\n{'#' * level} {text}\n\n")


def _render_list(node: Tag, parts: list[str], *, ordered: bool) -> None:
    """Emit ``<ul>`` / ``<ol>`` as a markdown list. Nested lists not supported."""
    parts.append("\n")
    for i, li in enumerate(node.find_all("li", recursive=False), start=1):
        prefix = f"{i}. " if ordered else "- "
        text = _inline_text(li)
        parts.append(f"{prefix}{text}\n")
    parts.append("\n")


def _render_table(node: Tag) -> str:
    """Convert a <table> to markdown. First row is header; pipes in cells are escaped."""
    rows = node.find_all("tr")
    if not rows:
        return ""

    def _cells(tr: Tag) -> list[str]:
        out = []
        for c in tr.find_all(["th", "td"], recursive=False):
            out.append(_inline_text(c).replace("|", r"\|"))
        return out

    body = [_cells(r) for r in rows]
    body = [r for r in body if r]
    if not body:
        return ""

    width = max(len(r) for r in body)
    body = [r + [""] * (width - len(r)) for r in body]

    header, *rest = body
    lines = ["", "| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for r in rest:
        lines.append("| " + " | ".join(r) + " |")
    lines.append("")
    return "\n".join(lines)


def _inline_text(node: Tag) -> str:
    """Render node's children as a flat inline string. Walks children to avoid handler recursion."""
    parts: list[str] = []
    for child in node.children:
        _render(child, parts)
    return " ".join("".join(parts).split())


def _cleanup(text: str) -> str:
    """Collapse triple-newlines, trim trailing whitespace on each line."""
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                out.append("")
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out).strip() + "\n"
