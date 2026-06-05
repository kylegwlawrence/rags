"""Extract <syntaxhighlight> and <math> blocks behind placeholders so other
string-level converters (lists, paragraphs, bold/italic) don't mangle their
contents. Placeholders are restored to final HTML at the end of the pipeline.

Both placeholders are block-shaped (``<div ...></div>``) when they need to
survive paragraph wrapping; inline math gets a ``<span>`` placeholder.
"""

import html
import re

_SYNTAX_RE = re.compile(
    r"<syntaxhighlight[^>]*>(.*?)</syntaxhighlight>",
    re.DOTALL | re.IGNORECASE,
)

_POEM_RE = re.compile(r"<poem\b[^>]*>(.*?)</poem>", re.DOTALL | re.IGNORECASE)

_MATH_BLOCK_RE = re.compile(
    r'<math\b[^>]*\bdisplay\s*=\s*["\']block["\'][^>]*>(.*?)</math>',
    re.DOTALL | re.IGNORECASE,
)
_MATH_INLINE_RE = re.compile(
    r"<math(?:\s[^>]*)?>(.*?)</math>",
    re.DOTALL | re.IGNORECASE,
)
# Chemistry notation: <chem>...</chem> and the legacy <ce>...</ce>. Bodies are
# mhchem syntax (e.g. "H2O", "2H2 + O2 -> 2H2O") which KaTeX renders via the
# vendored mhchem extension when wrapped as \ce{…}.
_CHEM_INLINE_RE = re.compile(
    r"<(chem|ce)(?:\s[^>]*)?>(.*?)</\1>",
    re.DOTALL | re.IGNORECASE,
)
# LaTeX environments that KaTeX requires be in display mode. Bare <math>...</math>
# (no display="block") whose body uses one of these must be promoted to a
# block-math placeholder; rendering them inline produces "can be used only in
# display mode" errors. Inline-safe environments (aligned, cases, pmatrix, ...)
# are intentionally absent.
_DISPLAY_ENV_RE = re.compile(
    r"\\begin\{(?:align|alignat|gather|multline|equation|eqnarray|CD)\*?\}",
)


def extract_syntaxhighlight(text: str) -> tuple[str, dict[str, str]]:
    """Replace <syntaxhighlight> blocks with placeholders pointing at <pre><code>."""
    blocks: dict[str, str] = {}
    counter = 0

    def replace(m: re.Match) -> str:
        nonlocal counter
        escaped = html.escape(m.group(1).strip())
        placeholder = f'<div data-codeblock="{counter}"></div>'
        blocks[placeholder] = f"<pre><code>{escaped}</code></pre>"
        counter += 1
        return placeholder

    return _SYNTAX_RE.sub(replace, text), blocks


def restore_code_blocks(text: str, blocks: dict[str, str]) -> str:
    for placeholder, code_html in blocks.items():
        text = text.replace(placeholder, code_html)
    return text


def extract_poem_tags(text: str) -> tuple[str, dict[str, str]]:
    """Extract ``<poem>...</poem>`` blocks behind block-shaped placeholders.

    Poem content has its inline wikitext (bold/italic, wikilinks) converted
    immediately so the final HTML can be restored verbatim later — without
    block-level converters touching it (which would treat ``*`` at start of
    line as a bullet, ``;`` as a definition term, etc.). Newlines become
    ``<br>``; blank lines become empty `<br>` (stanza separators).
    """
    # Local import keeps protect.py free of a static dependency on inline.py,
    # which is only needed here for poem-body processing.
    from rag.wiki_render.inline import convert_bold_italic, convert_links

    blocks: dict[str, str] = {}

    def replace(m: re.Match) -> str:
        raw = m.group(1).strip("\n")
        lines: list[str] = []
        for line in raw.split("\n"):
            lines.append(convert_links(convert_bold_italic(line)))
        body = "<br>".join(lines)
        idx = len(blocks)
        placeholder = f'<div data-poem="{idx}"></div>'
        blocks[placeholder] = f'<div class="poem">{body}</div>'
        return placeholder

    return _POEM_RE.sub(replace, text), blocks


def restore_poem_tags(text: str, blocks: dict[str, str]) -> str:
    for placeholder, poem_html in blocks.items():
        text = text.replace(placeholder, poem_html)
    return text


def extract_math_tags(text: str) -> tuple[str, dict[str, str]]:
    """Replace <math> tags with placeholders pointing at KaTeX delimiters.

    Block math (display="block", or body containing a display-only LaTeX
    environment like \\begin{align}) yields a <div> placeholder; inline math
    yields a <span> placeholder.
    """
    blocks: dict[str, str] = {}

    def _emit_block(content: str) -> str:
        idx = len(blocks)
        placeholder = f'<div data-mathblock="{idx}"></div>'
        blocks[placeholder] = f'<div class="math-display">$$\n{content}\n$$</div>'
        return placeholder

    def replace_block(m: re.Match) -> str:
        return _emit_block(m.group(1).strip())

    def replace_inline(m: re.Match) -> str:
        content = m.group(1).strip()
        # Promote to display math when the body uses a display-only environment
        # — KaTeX refuses to render these in inline mode.
        if _DISPLAY_ENV_RE.search(content):
            return _emit_block(content)
        idx = len(blocks)
        placeholder = f'<span data-mathinline="{idx}"></span>'
        blocks[placeholder] = f"\\({content}\\)"
        return placeholder

    def replace_chem(m: re.Match) -> str:
        content = m.group(2).strip()
        idx = len(blocks)
        placeholder = f'<span data-mathinline="{idx}"></span>'
        blocks[placeholder] = f"\\(\\ce{{{content}}}\\)"
        return placeholder

    text = _MATH_BLOCK_RE.sub(replace_block, text)
    text = _MATH_INLINE_RE.sub(replace_inline, text)
    text = _CHEM_INLINE_RE.sub(replace_chem, text)
    return text, blocks


def restore_math_tags(text: str, blocks: dict[str, str]) -> str:
    for placeholder, rendered in blocks.items():
        text = text.replace(placeholder, rendered)
    return text
