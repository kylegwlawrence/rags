"""Convert wikitext ``{| ... |}`` tables to HTML."""

import html
import re

from rag.wiki_render.inline import convert_bold_italic, convert_links

_TABLE_OPEN_RE = re.compile(r"^[:\s]*\{\|")
_TABLE_INNER_OPEN_RE = re.compile(r"^\s*\{\|")
_TABLE_INNER_CLOSE_RE = re.compile(r"^\s*\|\}")


def convert_tables(text: str) -> str:
    """Find {| ... |} blocks (including ``:{|`` indented form) and render them."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _TABLE_OPEN_RE.match(lines[i]):
            # Strip leading colon/whitespace from opener so the helper sees ``{|``.
            block = [lines[i].lstrip(":").lstrip()]
            i += 1
            depth = 1
            while i < len(lines) and depth > 0:
                if _TABLE_INNER_OPEN_RE.match(lines[i]):
                    depth += 1
                elif _TABLE_INNER_CLOSE_RE.match(lines[i]):
                    depth -= 1
                block.append(lines[i])
                i += 1
            if depth > 0:
                # Unclosed table — emit raw lines so no content is swallowed.
                out.extend(block)
            else:
                out.append(_table_to_html(block))
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _parse_table_attributes(opening_line: str) -> dict[str, str]:
    """Parse attributes from a {| opening line, defaulting class to wikitable."""
    line = opening_line.strip()
    if line.startswith("{|"):
        line = line[2:].strip()

    attrs: dict[str, str] = {}
    m = re.search(r'class=["\']([^"\']+)["\']', line)
    attrs["class"] = m.group(1) if m else "wikitable"
    return attrs


def parse_cell(cell: str, is_header: bool = False) -> dict:
    """Parse a single table cell into a dict of {content, align, colspan, rowspan, style}."""
    cell = cell.strip()
    result: dict = {
        "content": "",
        "align": None,
        "colspan": None,
        "rowspan": None,
        "style": None,
    }

    # Cells may carry attributes before content: ``attrs | content``. The pipe
    # is only an attribute separator when it appears outside any wikilink.
    pipe_idx = cell.find("|")
    if pipe_idx != -1 and "[" not in cell[:pipe_idx]:
        attrs_part = cell[:pipe_idx]
        content_part = cell[pipe_idx + 1 :].strip()

        m = re.search(r'align=["\']?(\w+)["\']?', attrs_part)
        if m:
            result["align"] = m.group(1)

        m = re.search(r"text-align:\s*(\w+)", attrs_part)
        if m:
            result["align"] = m.group(1)

        m = re.search(r'colspan=["\']?(\d+)["\']?', attrs_part)
        if m:
            result["colspan"] = int(m.group(1))

        m = re.search(r'rowspan=["\']?(\d+)["\']?', attrs_part)
        if m:
            result["rowspan"] = int(m.group(1))

        m = re.search(r'background:\s*([^;"|]+)', attrs_part)
        if m:
            result["style"] = f"background:{m.group(1).strip()}"

        result["content"] = html.escape(content_part)
    else:
        result["content"] = html.escape(cell)

    return result


def _render_cell(cell: dict, tag: str = "td") -> str:
    attrs: list[str] = []
    if cell.get("align"):
        attrs.append(f'class="align-{cell["align"]}"')
    if cell.get("colspan") and cell["colspan"] > 1:
        attrs.append(f'colspan="{cell["colspan"]}"')
    if cell.get("rowspan") and cell["rowspan"] > 1:
        attrs.append(f'rowspan="{cell["rowspan"]}"')
    if cell.get("style"):
        attrs.append(f'style="{cell["style"]}"')

    # Cell content was HTML-escaped on parse so we could safely store it; now
    # unescape so inline wikitext converters can do their work.
    content = html.unescape(cell["content"])
    content = convert_bold_italic(content)
    content = convert_links(content)

    attrs_str = " " + " ".join(attrs) if attrs else ""
    return f"<{tag}{attrs_str}>{content}</{tag}>"


def _table_to_html(table_lines: list[str]) -> str:
    table_attrs = _parse_table_attributes(table_lines[0] if table_lines else "")
    table_class = table_attrs.get("class", "")

    caption: str | None = None
    header_rows: list[list[dict]] = []
    body_rows: list[list[dict]] = []
    current_row: list[dict] = []
    nested_depth = 0
    in_header = False

    for idx, line in enumerate(table_lines):
        stripped = line.strip()

        if idx == 0:
            continue  # opening {| line, already consumed for attrs

        if stripped.startswith("{|"):
            nested_depth += 1
            continue
        if stripped.startswith("|}"):
            if nested_depth > 0:
                nested_depth -= 1
            continue
        if nested_depth > 0:
            continue  # skip nested table contents

        if stripped.startswith("|+"):
            caption = html.escape(stripped[2:].strip())
            continue

        if stripped.startswith("|-"):
            if current_row:
                (header_rows if in_header else body_rows).append(current_row)
                current_row = []
            in_header = False
            continue

        if stripped.startswith("!"):
            cells = [parse_cell(c, is_header=True) for c in re.split(r"!!", stripped[1:])]
            current_row.extend(cells)
            in_header = True
            continue

        if stripped.startswith("|"):
            cells = [parse_cell(c, is_header=False) for c in re.split(r"\|\|", stripped[1:])]
            current_row.extend(cells)
            continue

        if stripped and current_row:
            # Continuation of previous cell content.
            current_row[-1]["content"] += " " + html.escape(stripped)

    if current_row:
        if in_header or (not body_rows and not header_rows):
            header_rows.append(current_row)
        else:
            body_rows.append(current_row)

    if not header_rows and not body_rows:
        return ""

    # Promote first body row to header if the table didn't declare one.
    if not header_rows and body_rows:
        header_rows.append(body_rows.pop(0))

    parts = [f'<table class="{table_class}">']
    if caption:
        parts.append(f"<caption>{caption}</caption>")

    if header_rows:
        parts.append("<thead>")
        for row in header_rows:
            parts.append("<tr>")
            for cell in row:
                parts.append(_render_cell(cell, tag="th"))
            parts.append("</tr>")
        parts.append("</thead>")

    if body_rows:
        parts.append("<tbody>")
        for row in body_rows:
            parts.append("<tr>")
            for cell in row:
                parts.append(_render_cell(cell, tag="td"))
            parts.append("</tr>")
        parts.append("</tbody>")

    parts.append("</table>")
    return "\n".join(parts)
