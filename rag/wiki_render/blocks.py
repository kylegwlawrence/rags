"""Block-level converters that operate on the rendered wikitext string:
lists, headings, and paragraph wrapping.
"""

import html
import re

# Block-level HTML tags that _wrap_paragraphs must NOT wrap in <p>.
_BLOCK_TAGS = (
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "ul",
    "ol",
    "dl",
    "li",
    "dt",
    "dd",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "div",
    "p",
    "blockquote",
    "pre",
    "syntaxhighlight",
)


def convert_lists(text: str) -> str:
    """Convert wikitext lists to HTML (handles arbitrary nesting/mixing).

    *  → unordered, #  → ordered, ;/: → definition list.
    Last prefix character determines item type; prefix length is depth.
    """
    lines = text.split("\n")
    converted: list[str] = []
    stack: list[str] = []  # Open list types in order

    def close_to(level: int) -> None:
        while len(stack) > level:
            kind = stack.pop()
            if kind == "*":
                converted.append("</ul>")
            elif kind == "#":
                converted.append("</ol>")
            elif kind in (";", ":"):
                converted.append("</dl>")

    for line in lines:
        m = re.match(r"^([*#;:]+)(.*)", line)
        if not m:
            close_to(0)
            converted.append(line)
            continue

        prefix = m.group(1)
        # List content may contain inline HTML like <code>; don't escape.
        content = m.group(2).lstrip()

        target = list(prefix)

        # Find divergence point with current stack.
        common = 0
        for i in range(min(len(stack), len(target))):
            if stack[i] == target[i]:
                common += 1
            else:
                break
        close_to(common)

        # Open lists down to target depth.
        for i in range(common, len(target)):
            ch = target[i]
            if ch in (";", ":"):
                # Definition lists: ; and : share a single <dl>.
                if not stack or stack[-1] not in (";", ":"):
                    converted.append("<dl>")
                stack.append(ch)
            elif ch == "*":
                converted.append("<ul>")
                stack.append("*")
            elif ch == "#":
                converted.append("<ol>")
                stack.append("#")

        last = prefix[-1]
        if last in ("*", "#"):
            converted.append(f"<li>{content}</li>")
        elif last == ";":
            converted.append(f"<dt>{content}</dt>")
        elif last == ":":
            converted.append(f"<dd>{content}</dd>")

    close_to(0)
    return "\n".join(converted)


def _make_heading_id(heading_text: str) -> str:
    # Strip any HTML left from earlier conversions, then space → underscore
    # to match MediaWiki's anchor convention.
    heading_id = re.sub(r"<[^>]+>", "", heading_text)
    heading_id = heading_id.strip().replace(" ", "_")
    return html.escape(heading_id, quote=True)


def convert_headings(text: str) -> str:
    """Convert == Heading == lines to <hN id="...">Heading</hN>."""
    # Iterate from highest level (6 equals) down so we don't match partials.
    for level in range(6, 1, -1):
        equals = "=" * level
        pattern = rf"^{re.escape(equals)}\s*(.+?)\s*{re.escape(equals)}\s*$"

        def replace(m: re.Match, _level: int = level) -> str:
            inner = m.group(1).strip()
            return f'<h{_level} id="{_make_heading_id(inner)}">{inner}</h{_level}>'

        text = re.sub(pattern, replace, text, flags=re.MULTILINE)
    return text


def _is_block_tag(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("<"):
        return False
    for tag in _BLOCK_TAGS:
        if stripped.startswith(f"<{tag}") or stripped.startswith(f"</{tag}"):
            return True
    return False


def wrap_paragraphs(text: str) -> str:
    """Wrap consecutive non-block lines in <p>...</p>.

    Block-level HTML (tables, headings, lists, code/math placeholders) is
    passed through untouched so we don't double-wrap.
    """
    lines = text.split("\n")
    result: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            content = " ".join(line.strip() for line in pending if line.strip())
            if content:
                result.append(f"<p>{content}</p>")
            pending.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
        elif _is_block_tag(stripped):
            flush()
            result.append(line)
        else:
            pending.append(stripped)
    flush()

    return "\n".join(result)
