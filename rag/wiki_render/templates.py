"""Wikicode-level template handlers.

These run before any string-level conversion so the structured representation
of templates is preserved. Each ``convert_*_templates`` function walks the
wikicode and replaces matching templates with HTML or wikilink strings.

Anything not handled here is removed wholesale by ``strip.strip_templates``
in the next pipeline stage.
"""

import html
import re

import mwparserfromhell

from rag.wiki_render.data import (
    CITE_TEMPLATE_PREFIXES,
    IMAGE_FIELD_PREFIXES,
    IMAGE_VALUE_RE,
    INDICATORS,
    LATEX_MATH_TEMPLATE_NAMES,
    MATH_TEMPLATE_NAMES,
    MONTH_NAMES,
    MVAR_TEMPLATE_NAMES,
    PASSTHROUGH_FIRST_ARG_TEMPLATES,
    TAXONOMY_TEMPLATE_NAMES,
    UNIT_NAMES,
    lang_code_to_name,
)
from rag.wiki_render.inline import convert_bold_italic, convert_links

# Matches <ref name="X">content</ref>, with name in any of three quoting styles.
_REF_TAG_RE = re.compile(
    r'<ref\s+name\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^>\s]+))\s*>(.*?)</ref>',
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _render_lang(code: str, text: str) -> str:
    """Format a {{lang}} / {{langx}} pair as 'Language: <em>text</em>'."""
    return f"{lang_code_to_name(code)}: <em>{text}</em>"


def _render_ref_body(contents: str) -> str:
    """Render the inside of a <ref>...</ref>: cite templates → formatted strings,
    fallback to escaped plaintext (with bare {{...}} stripped).
    """
    sub = mwparserfromhell.parse(contents)
    cite_parts = []
    for tmpl in sub.filter_templates():
        name = str(tmpl.name).strip().lower()
        if any(name.startswith(p) for p in CITE_TEMPLATE_PREFIXES):
            formatted = format_cite_template(tmpl)
            if formatted:
                cite_parts.append(formatted)
    if cite_parts:
        return " ".join(cite_parts)
    return html.escape(re.sub(r"\{\{[^}]*\}\}", "", contents).strip())


# ---------------------------------------------------------------------------
# Citation template formatting
# ---------------------------------------------------------------------------


def format_cite_template(template) -> str:
    """Render a {{cite ...}} / {{citation}} template as a flat HTML string."""
    fields: dict[str, str] = {}
    for param in template.params:
        key = str(param.name).strip().lower()
        val = str(param.value).strip()
        if val:
            fields[key] = val

    parts: list[str] = []

    # Author handling cascade:
    #   author=         single name, wins outright
    #   authors=        comma- or semicolon-separated list (kept verbatim)
    #   vauthors=       Vancouver style ("Smith J, Doe AB") — comma-separated
    #   last/first      single author split into surname + given names
    #   last1/first1 … last9/first9    indexed multi-author form
    author = fields.get("author") or fields.get("authors") or fields.get("vauthors")
    if not author:
        if "last" in fields:
            author = (fields.get("last", "") + ", " + fields.get("first", "")).strip(", ")
        else:
            authors: list[str] = []
            for i in range(1, 10):
                last = fields.get(f"last{i}")
                first = fields.get(f"first{i}")
                if last:
                    authors.append((last + ", " + first).strip(", ") if first else last)
                elif not authors:
                    break
            author = "; ".join(authors) if authors else ""
    if author:
        parts.append(html.escape(author))

    title = fields.get("title", "")
    url = fields.get("url", "")
    if title and url:
        parts.append(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
            f'target="_blank">{html.escape(title)}</a>'
        )
    elif title:
        parts.append(f"<em>{html.escape(title)}</em>")
    elif url:
        parts.append(
            f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" target="_blank">{html.escape(url)}</a>'
        )

    for field in ("work", "website", "journal", "newspaper", "magazine", "publisher"):
        if field in fields:
            parts.append(html.escape(fields[field]))
            break

    if "date" in fields:
        parts.append(html.escape(fields["date"]))

    return ". ".join(parts)


def convert_citation_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace top-level {{cite ...}} templates in body text with formatted HTML.

    ``recursive=False`` keeps cites nested inside <ref> tags untouched — those
    are handled by ``collect_inline_refs``.
    """
    for template in wikicode.filter_templates(recursive=False):
        name = str(template.name).strip().lower()
        if any(name.startswith(p) for p in CITE_TEMPLATE_PREFIXES):
            formatted = format_cite_template(template)
            if formatted:
                try:
                    wikicode.replace(template, formatted)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# Reference / Reflist
# ---------------------------------------------------------------------------


def collect_inline_refs(
    wikicode: mwparserfromhell.wikicode.Wikicode,
) -> list[tuple[str | None, str]]:
    """Collect inline <ref> tags from the article body, in citation order.

    Self-closing back-refs (<ref name="X"/>) are resolved to their content if
    they were defined inside a {{Reflist|refs=...}} parameter or inside a
    <references>...</references> body; otherwise they are skipped (the inline
    definition is collected at its definition site).

    Returns a list of (name_or_None, rendered_html). No deduplication.
    """
    # Build a name → content lookup for refs defined only as definitions
    # (refs= param of {{Reflist}} or body of <references>).
    refs_definitions: dict[str, str] = {}
    for tmpl in wikicode.filter_templates():
        if str(tmpl.name).strip().lower() != "reflist":
            continue
        rp = next((p for p in tmpl.params if str(p.name).strip() == "refs"), None)
        if not rp:
            continue
        for m in _REF_TAG_RE.finditer(str(rp.value)):
            name = m.group(1) or m.group(2) or m.group(3)
            content = m.group(4).strip()
            if name not in refs_definitions:
                refs_definitions[name] = content

    for tag in wikicode.filter_tags(recursive=False):
        if str(tag.tag).strip().lower() != "references" or tag.self_closing:
            continue
        for m in _REF_TAG_RE.finditer(str(tag.contents)):
            name = m.group(1) or m.group(2) or m.group(3)
            content = m.group(4).strip()
            if name not in refs_definitions:
                refs_definitions[name] = content

    collected: list[tuple[str | None, str]] = []

    for tag in wikicode.filter_tags(recursive=False):
        if str(tag.tag).strip().lower() != "ref":
            continue

        name = str(tag.get("name").value).strip() if tag.has("name") else None

        if tag.self_closing:
            # Resolve only if content was defined as a standalone definition.
            # Inline definitions are already collected when their full tag is hit.
            if name and name in refs_definitions:
                contents = refs_definitions[name]
            else:
                continue
        else:
            contents = str(tag.contents).strip()
            if not contents:
                continue

        rendered = _render_ref_body(contents)
        if rendered:
            collected.append((name, rendered))

    return collected


def convert_reflist_template(
    wikicode: mwparserfromhell.wikicode.Wikicode,
    collected_refs: list[tuple[str | None, str]] | None = None,
) -> None:
    """Convert {{Reflist}} templates to <ol class="references">.

    Two modes:
      - ``refs=`` parameter present: render each ref defined there, prepended by
        any inline refs collected from the body.
      - bare {{Reflist}}: render only the inline refs.

    Non-Reflist templates are left for ``strip.strip_templates`` to remove.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != "reflist":
            continue

        refs_param = next(
            (p for p in template.params if str(p.name).strip() == "refs"),
            None,
        )

        items: list[str] = []

        # Inline refs first, in body citation order.
        if collected_refs:
            for idx, (name, rendered) in enumerate(collected_refs, start=1):
                ref_id = html.escape(name, quote=True) if name else str(idx)
                items.append(f'<li id="ref_{ref_id}">{rendered}</li>')

        # Then refs defined in the refs= parameter.
        if refs_param:
            for m in _REF_TAG_RE.finditer(str(refs_param.value)):
                ref_name = m.group(1) or m.group(2) or m.group(3)
                ref_content = m.group(4).strip()
                try:
                    rendered = _render_ref_body(ref_content)
                except Exception:
                    rendered = html.escape(ref_content)
                if rendered:
                    items.append(f'<li id="ref_{html.escape(ref_name, quote=True)}">{rendered}</li>')

        replacement = '<ol class="references">\n' + "\n".join(items) + "\n</ol>" if items else ""
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


_REFERENCES_OPEN_CLOSED_RE = re.compile(
    r"<references\b[^>]*>(.*?)</references>",
    re.DOTALL | re.IGNORECASE,
)
_REFERENCES_SELF_CLOSED_RE = re.compile(r"<references\b[^>]*/>", re.IGNORECASE)


def convert_references_tag(
    text: str,
    collected_refs: list[tuple[str | None, str]] | None,
) -> str:
    """Render bare ``<references />`` / ``<references>...</references>`` tags.

    Articles that use the raw HTML tag instead of ``{{Reflist}}`` lose their
    reference list otherwise — ``strip_refs`` would remove the tag and the
    collected inline refs would never appear in the output. This pass mirrors
    :func:`convert_reflist_template` at the string level and runs *before*
    ``strip_refs``.

    If a ``{{Reflist}}`` already rendered (detected via ``<ol
    class="references">`` already in the text), subsequent ``<references>``
    tags are dropped rather than duplicating the list.
    """
    rendered_once = ['<ol class="references">' in text]

    def render_list(body_text: str) -> str:
        if rendered_once[0]:
            return ""
        items: list[str] = []
        if collected_refs:
            for idx, (name, rendered) in enumerate(collected_refs, start=1):
                ref_id = html.escape(name, quote=True) if name else str(idx)
                items.append(f'<li id="ref_{ref_id}">{rendered}</li>')
        if body_text:
            for m in _REF_TAG_RE.finditer(body_text):
                ref_name = m.group(1) or m.group(2) or m.group(3)
                ref_content = m.group(4).strip()
                try:
                    rendered = _render_ref_body(ref_content)
                except Exception:
                    rendered = html.escape(ref_content)
                if rendered:
                    items.append(f'<li id="ref_{html.escape(ref_name, quote=True)}">{rendered}</li>')
        if not items:
            return ""
        rendered_once[0] = True
        return '<ol class="references">\n' + "\n".join(items) + "\n</ol>"

    text = _REFERENCES_OPEN_CLOSED_RE.sub(lambda m: render_list(m.group(1)), text)
    text = _REFERENCES_SELF_CLOSED_RE.sub(lambda m: render_list(""), text)
    return text


# ---------------------------------------------------------------------------
# Math / code / lang / indicator / section-link
# ---------------------------------------------------------------------------


_MATH_TEMPLATE_OPEN_RE = re.compile(
    r"\{\{\s*(" + "|".join(re.escape(n) for n in MATH_TEMPLATE_NAMES) + r")\s*([|}])",
    re.IGNORECASE,
)


def _find_template_close(text: str, content_start: int) -> int | None:
    """Find the matching ``}}`` for a template whose body starts at ``content_start``.

    Tracks the count of unmatched inner single braces so that LaTeX bodies like
    ``\\frac{a}{b}}}`` close on the *outer* ``}}`` rather than gobbling the
    body's last ``}`` — the failure mode in mwparserfromhell that this pre-pass
    exists to work around. Returns the index of the first ``}`` of the closing
    pair, or ``None`` if unbalanced.
    """
    n = len(text)
    inner = 0
    i = content_start
    while i < n - 1:
        c = text[i]
        if c == "{":
            inner += 1
            i += 1
        elif c == "}":
            if inner == 0 and text[i + 1] == "}":
                return i
            if inner > 0:
                inner -= 1
            i += 1
        else:
            i += 1
    return None


def _math_first_positional(params: str) -> str:
    """Return the substring before the first top-level ``|`` in a template body.

    Brace-nested ``|`` characters (e.g., inside a nested ``{{val|5}}``) are
    skipped so they don't terminate the positional. If there is no top-level
    ``|``, the entire body is returned — this preserves ``=`` characters inside
    math expressions like ``{{math|a = b}}``.
    """
    inner = 0
    for i, c in enumerate(params):
        if c == "{":
            inner += 1
        elif c == "}":
            if inner > 0:
                inner -= 1
        elif c == "|" and inner == 0:
            return params[:i]
    return params


def _substitute_escape_templates(content: str) -> str:
    """Inline MediaWiki magic-word escape templates inside an HTML-math body.

    ``{{=}}`` → ``=``, ``{{!}}`` → ``|``, ``{{!-}}`` → ``|-``, ``{{!!}}`` → ``||``.
    Longer forms run first so ``{{!}}`` doesn't shadow ``{{!-}}`` / ``{{!!}}``.
    Only meaningful for HTML-math bodies; LaTeX bodies should not contain these.
    """
    return content.replace("{{=}}", "=").replace("{{!-}}", "|-").replace("{{!!}}", "||").replace("{{!}}", "|")


def _emit_math_template(name: str, content: str) -> str:
    """Render the extracted body of a math-family template as HTML.

    Three rendering modes, chosen by ``name``:

    * **LaTeX-math** (``tmath``, ``tmath block``) — body is raw LaTeX; emit
      ``<math>…</math>`` so the existing math-tag protector wraps it in KaTeX
      delimiters.
    * **mvar** — body is always italic (single math variable); emit
      ``<span class="texhtml"><i>…</i></span>``.
    * **HTML-math** (``math``, ``math block``, ``bigmath``) — body is wikitext
      (apostrophe-italics, ``<sup>``, escape templates); emit
      ``<span class="texhtml">…</span>`` and let the downstream pipeline handle
      its formatting markup.
    """
    if name in LATEX_MATH_TEMPLATE_NAMES:
        return f"<math>{content}</math>"
    content = _substitute_escape_templates(content)
    if name in MVAR_TEMPLATE_NAMES:
        return f'<span class="texhtml"><i>{content}</i></span>'
    return f'<span class="texhtml">{content}</span>'


def replace_math_templates(wikitext: str) -> str:
    """Replace the ``{{math|…}}`` / ``{{tmath|…}}`` / ``{{mvar|…}}`` template
    family with their respective HTML wrappers **before** mwparserfromhell sees
    the input.

    Two reasons for the string-level pre-pass:

    1. mwparserfromhell uses greedy double-brace matching, so a body like
       ``\\frac{a}{b}}}`` fuses the LaTeX body's final ``}`` into the
       template's closing ``}}``, truncating ``template.params[0]``.
    2. We need to distinguish HTML-rendered math (wikitext body, ``texhtml``
       span) from LaTeX-rendered math (KaTeX-bound ``<math>`` tag) — see
       :func:`_emit_math_template`.
    """
    out: list[str] = []
    pos = 0
    while True:
        m = _MATH_TEMPLATE_OPEN_RE.search(wikitext, pos)
        if not m:
            out.append(wikitext[pos:])
            break
        out.append(wikitext[pos : m.start()])
        name = m.group(1).lower()
        if m.group(2) == "}":
            # ``{{name}}`` with no body — emit an empty wrapper and resume after ``}}``.
            close = m.end() - 1
            if close + 1 < len(wikitext) and wikitext[close + 1] == "}":
                out.append(_emit_math_template(name, ""))
                pos = close + 2
                continue
            # Malformed — single trailing ``}``; leave the source untouched.
            out.append(wikitext[m.start() : m.end()])
            pos = m.end()
            continue
        # group(2) == "|": content starts after the `|`
        content_start = m.end()
        close = _find_template_close(wikitext, content_start)
        if close is None:
            # Unbalanced; leave source untouched so the user sees the error.
            out.append(wikitext[m.start() : m.end()])
            pos = m.end()
            continue
        content = _math_first_positional(wikitext[content_start:close])
        out.append(_emit_math_template(name, content))
        pos = close + 2
    return "".join(out)


def convert_code_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{code|...}}, {{tt|...}}, etc. with <code>...</code>.

    Handles both ``{{code|x = 1}}`` (positional) and ``{{code|lang=python|x = 1}}``
    (named lang parameter alongside positional content).
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ("code", "codes", "codett", "c", "mono", "tt", "kbd"):
            continue
        params = list(template.params)
        if not params:
            continue

        # Walk params in reverse — the code content is typically the last
        # positional param. Anything that *looks* like ``key=value`` (alpha
        # key) is treated as metadata and skipped.
        code_content: str | None = None
        for param in reversed(params):
            param_str = str(param).strip()
            if "=" not in param_str or not param_str.split("=")[0].strip().isalpha():
                code_content = param_str.split("=", 1)[1].strip() if "=" in param_str else param_str
                break

        if code_content:
            try:
                wikicode.replace(template, f"<code>{html.escape(code_content)}</code>")
            except ValueError:
                pass


def convert_lang_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{lang|XX|text}} / {{langx|XX|…|text}} / {{lang-XX|text}} with HTML.

    The ``{{lang-XX|text}}`` shorthand encodes the language in the template
    name (e.g. ``lang-fr``, ``lang-de``). We recognise that pattern as well so
    the language label appears in output even for the shorthand form.
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]

        if name in ("lang", "langx"):
            # First positional is the language code; last is the text.
            replacement = _render_lang(positional[0], positional[-1]) if len(positional) >= 2 else None
        elif name.startswith("lang-") and len(name) > 5:
            # Single positional holds the text; language code is in the name.
            code = name[5:]
            replacement = _render_lang(code, positional[-1]) if positional else None
        else:
            continue

        try:
            if replacement:
                wikicode.replace(template, replacement)
            else:
                wikicode.remove(template)
        except ValueError:
            pass


def convert_passthrough_first_arg_templates(
    wikicode: mwparserfromhell.wikicode.Wikicode,
) -> None:
    """Render templates from ``PASSTHROUGH_FIRST_ARG_TEMPLATES`` as just their
    first positional arg.

    For content-bearing wrappers like ``{{quote|"text"|author}}`` the body text
    is what readers want; attribution/styling parameters are dropped. Templates
    not in the allowlist remain untouched (and get stripped later as usual).
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in PASSTHROUGH_FIRST_ARG_TEMPLATES:
            continue
        value = _first_positional(template) or ""
        try:
            if value:
                wikicode.replace(template, value)
            else:
                wikicode.remove(template)
        except ValueError:
            pass


def convert_wikidata_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Strip {{wikidata|...}} templates.

    These fetch live property values from Wikidata via API; no network access is
    available here, so we remove them rather than render stale or empty output.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() == "wikidata":
            try:
                wikicode.remove(template)
            except ValueError:
                pass


def convert_indicator_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace status templates ({{yes}}, {{no}}, {{partial}}, ...) with
    a <span> carrying a CSS class for table styling.
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name in INDICATORS:
            text, css_class = INDICATORS[name]
            try:
                wikicode.replace(template, f'<span class="{css_class}">{text}</span>')
            except ValueError:
                pass


def convert_annotated_link_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{annotated link|Title|Label}} with the equivalent [[wikilink]].

    Output is wikitext — the link converter picks it up later in the pipeline.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != "annotated link":
            continue
        params = list(template.params)
        if not params:
            continue
        target = str(params[0].value).strip()
        label = str(params[1].value).strip() if len(params) > 1 else None
        replacement = f"[[{target}|{label}]]" if label else f"[[{target}]]"
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


def convert_section_link_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{Section link|Page#Section}} with the equivalent [[wikilink]].

    Output is wikitext — the link converter picks it up later in the pipeline.
    """
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != "section link":
            continue
        params = list(template.params)
        if not params:
            continue
        target = str(params[0]).strip()
        label = str(params[1]).strip() if len(params) > 1 else None
        replacement = f"[[{target}|{label}]]" if label else f"[[{target}]]"
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _first_positional(template) -> str | None:
    """Return the value of the first positional parameter, or None."""
    for p in template.params:
        if str(p.name).strip().isdigit():
            return str(p.value).strip()
    return None


# ---------------------------------------------------------------------------
# Hatnote templates ({{main}}, {{see also}}, {{further}}, {{about}})
# ---------------------------------------------------------------------------


def convert_hatnote_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace hatnote templates with <div class="hatnote"> elements."""
    PREFIXES = {
        "main": "Main article",
        "see also": "See also",
        "further": "Further information",
        "see": "See",
    }
    for template in wikicode.filter_templates(recursive=False):
        name = str(template.name).strip().lower()

        if name == "about":
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            if not positional:
                try:
                    wikicode.remove(template)
                except ValueError:
                    pass
                continue
            parts = [f"This article is about {positional[0]}."] if positional[0] else []
            for i in range(1, len(positional) - 1, 2):
                use = positional[i]
                article = positional[i + 1] if i + 1 < len(positional) else ""
                if use and article:
                    parts.append(f"For {use}, see [[{article}]].")
                elif article:
                    parts.append(f"See [[{article}]].")
            replacement = f'<div class="hatnote">{" ".join(parts)}</div>' if parts else ""

        elif name == "hatnote":
            content = _first_positional(template) or ""
            replacement = f'<div class="hatnote">{content}</div>' if content else ""

        elif name in PREFIXES:
            prefix = PREFIXES[name]
            articles = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            links = ", ".join(f"[[{a}]]" for a in articles if a)
            replacement = f'<div class="hatnote">{prefix}: {links}</div>' if links else ""

        else:
            continue

        try:
            if replacement:
                wikicode.replace(template, replacement)
            else:
                wikicode.remove(template)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Simple inline templates → HTML equivalents
# ---------------------------------------------------------------------------


def convert_simple_inline_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Convert simple wrapping/annotation templates to HTML."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()

        if name == "small":
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, f"<small>{content}</small>")
                except ValueError:
                    pass

        elif name == "sup":
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, f"<sup>{content}</sup>")
                except ValueError:
                    pass

        elif name in ("blockquote", "quote", "bq"):
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, f"<blockquote>{content}</blockquote>")
                except ValueError:
                    pass

        elif name == "nbsp":
            try:
                wikicode.replace(template, " ")
            except ValueError:
                pass

        elif name in ("as of", "asof"):
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            year_val = positional[0] if positional else ""
            month_val = positional[1] if len(positional) >= 2 else ""
            day_val = positional[2] if len(positional) >= 3 else ""
            try:
                month_name = MONTH_NAMES[int(month_val)] if month_val else ""
            except (ValueError, IndexError):
                month_name = month_val
            if day_val and month_name:
                date_str = f"{month_name} {day_val}, {year_val}"
            elif month_name:
                date_str = f"{month_name} {year_val}"
            else:
                date_str = year_val
            asof_replacement = f"as of {date_str}" if date_str else "as of"
            try:
                wikicode.replace(template, asof_replacement)
            except ValueError:
                pass

        elif name in ("circa", "c.", "ca", "ca."):
            year = _first_positional(template) or ""
            replacement = f"c. {year}" if year else "c."
            try:
                wikicode.replace(template, replacement)
            except ValueError:
                pass

        elif name in ("in lang", "in language"):
            lang_param = _first_positional(template) or ""
            if lang_param:
                lang_name = lang_code_to_name(lang_param)
                try:
                    wikicode.replace(template, f"(in {lang_name})")
                except ValueError:
                    pass
            else:
                try:
                    wikicode.remove(template)
                except ValueError:
                    pass

        elif name == "official website":
            url = _first_positional(template) or ""
            if url:
                safe_url = html.escape(url, quote=True)
                try:
                    wikicode.replace(
                        template,
                        f'<a href="{safe_url}" rel="noopener noreferrer" target="_blank">Official website</a>',
                    )
                except ValueError:
                    pass
            else:
                try:
                    wikicode.remove(template)
                except ValueError:
                    pass

        elif name == "rn":
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, f'<span style="font-variant:small-caps">{content}</span>')
                except ValueError:
                    pass

        elif name in ("nowrap", "nowr", "nobr", "no wrap"):
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, f'<span style="white-space:nowrap">{content}</span>')
                except ValueError:
                    pass

        elif name in ("ipa", "ipac-en", "ipa-en", "ipa-all"):
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, content)
                except ValueError:
                    pass

        elif name in ("nts", "ntsh"):
            content = _first_positional(template)
            if content is not None:
                try:
                    wikicode.replace(template, content)
                except ValueError:
                    pass

        elif name == "sort":
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            display = positional[1] if len(positional) >= 2 else (positional[0] if positional else None)
            if display is not None:
                try:
                    wikicode.replace(template, display)
                except ValueError:
                    pass

        elif name == "sortname":
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            if positional:
                display = " ".join(p for p in positional[:2] if p)
                try:
                    wikicode.replace(template, display)
                except ValueError:
                    pass

        elif name in ("tooltip", "abbr"):
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            if positional:
                text_val = positional[0]
                tip = positional[1] if len(positional) >= 2 else ""
                replacement = f'<abbr title="{html.escape(tip)}">{text_val}</abbr>' if tip else text_val
                try:
                    wikicode.replace(template, replacement)
                except ValueError:
                    pass

        elif name in ("frac", "fraction"):
            positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
            if len(positional) == 1:
                replacement = f"<sup>1</sup>⁄<sub>{positional[0]}</sub>"
            elif len(positional) == 2:
                replacement = f"<sup>{positional[0]}</sup>⁄<sub>{positional[1]}</sub>"
            elif len(positional) >= 3:
                replacement = f"{positional[0]} <sup>{positional[1]}</sup>⁄<sub>{positional[2]}</sub>"
            else:
                replacement = None
            if replacement is not None:
                try:
                    wikicode.replace(template, replacement)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# List templates in body text ({{plainlist}}, {{hlist}})
# ---------------------------------------------------------------------------


def convert_list_body_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Convert {{plainlist}} and {{hlist}} in body text to HTML list markup."""
    for template in wikicode.filter_templates(recursive=False):
        name = str(template.name).strip().lower()

        if name in ("plainlist", "flatlist"):
            for p in template.params:
                pname = str(p.name).strip()
                if pname in ("class", "style", "indent"):
                    continue
                items = [
                    f"<li>{line.strip().lstrip('*#:').strip()}</li>"
                    for line in str(p.value).split("\n")
                    if line.strip().lstrip("*#:").strip()
                ]
                if items:
                    replacement = '<ul class="plainlist">\n' + "\n".join(items) + "\n</ul>"
                    try:
                        wikicode.replace(template, replacement)
                    except ValueError:
                        pass
                break

        elif name == "hlist":
            items = [
                str(p.value).strip()
                for p in template.params
                if str(p.name).strip()
                not in ("class", "style", "ul_style", "li_style", "indent", "item_style", "first_style")
                and str(p.value).strip()
            ]
            if items:
                try:
                    wikicode.replace(template, " · ".join(items))
                except ValueError:
                    pass
            else:
                try:
                    wikicode.remove(template)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# {{convert}} / {{cvt}} — unit conversion display
# ---------------------------------------------------------------------------

_RANGE_CONNECTORS = frozenset({"to", "and", "-", "–", "or", "+", "by", "x", "×"})


def convert_convert_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{convert|value|unit|...}} with 'value unit' plain text."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ("convert", "cvt"):
            continue

        positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]

        if not positional:
            try:
                wikicode.remove(template)
            except ValueError:
                pass
            continue

        if len(positional) >= 3 and positional[1].lower() in _RANGE_CONNECTORS:
            val1, val2 = positional[0], positional[2]
            from_unit = positional[3] if len(positional) >= 4 else ""
            unit_display = UNIT_NAMES.get(from_unit, from_unit)
            replacement = f"{val1}–{val2}"
            if unit_display:
                replacement += f" {unit_display}"
        else:
            val = positional[0]
            from_unit = positional[1] if len(positional) > 1 else ""
            unit_display = UNIT_NAMES.get(from_unit, from_unit)
            replacement = f"{val}"
            if unit_display:
                replacement += f" {unit_display}"

        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Flag / country templates
# ---------------------------------------------------------------------------


def convert_flag_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace flag/country templates with a plain wikilink to the country.

    {{flag|France}} → [[France]]
    {{flagicon|France}} → removed (image-only, no text value)
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name in ("flag", "flagu", "flagcountry", "country"):
            country = _first_positional(template) or ""
            try:
                if country:
                    wikicode.replace(template, f"[[{country}]]")
                else:
                    wikicode.remove(template)
            except ValueError:
                pass
        elif name in ("flagicon", "flag icon", "flagathlete", "flagioc", "flagiocathlete", "flagnation"):
            try:
                wikicode.remove(template)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Geographic coordinates ({{coord}})
# ---------------------------------------------------------------------------


def _format_dms(parts: list[str]) -> str:
    """Render up to three degree/minute/second parts as '40°26′46″'."""
    units = ("°", "′", "″")
    return "".join(f"{p}{units[i]}" for i, p in enumerate(parts[:3]))


def _format_coord(positional: list[str]) -> str:
    """Render the positional params of a ``{{coord}}`` template as a string.

    Handles four common shapes:
      ``{{coord|D|N|D|W}}``                  — degree-only DMS-style
      ``{{coord|D|M|N|D|M|W}}``              — degree+minute
      ``{{coord|D|M|S|N|D|M|S|W}}``          — full DMS
      ``{{coord|lat|lon}}``                  — signed decimal
    Trailing positional metadata (``type:city_region:…``) and any named params
    (``display=``, ``name=``, ``format=``) are ignored.
    """
    if not positional:
        return ""

    lat_dir_idx = next((i for i, v in enumerate(positional) if v.upper() in ("N", "S")), None)
    lon_dir_idx = next((i for i, v in enumerate(positional) if v.upper() in ("E", "W")), None)

    if lat_dir_idx is not None and lon_dir_idx is not None and lat_dir_idx < lon_dir_idx:
        lat_parts = positional[:lat_dir_idx]
        lat_dir = positional[lat_dir_idx].upper()
        lon_parts = positional[lat_dir_idx + 1 : lon_dir_idx]
        lon_dir = positional[lon_dir_idx].upper()
        if lat_parts and lon_parts:
            return f"{_format_dms(lat_parts)}{lat_dir} {_format_dms(lon_parts)}{lon_dir}"
        return ""

    if len(positional) >= 2:
        try:
            lat = float(positional[0])
            lon = float(positional[1])
        except ValueError:
            return ""
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        return f"{abs(lat):g}°{lat_dir} {abs(lon):g}°{lon_dir}"

    return ""


def convert_sfn_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Render short-footnote templates ({{sfn}}, {{sfnp}}, {{sfnm}}) as numbered markers.

    Each occurrence gets an article-scoped sequential index — no deduplication
    against ``{{sfn|Smith|2010|p=42}}`` recurrences, since we don't yet emit a
    matching bibliography that a back-reference could resolve to.
    """
    counter = [0]
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ("sfn", "sfnp", "sfnm"):
            continue
        counter[0] += 1
        try:
            wikicode.replace(template, f'<sup class="sfn">[{counter[0]}]</sup>')
        except ValueError:
            pass


def convert_short_description_templates(
    wikicode: mwparserfromhell.wikicode.Wikicode,
) -> None:
    """Drop ``{{short description|…}}``.

    The short description is search/preview metadata, not body content — it
    typically duplicates the article's lead sentence. We keep it stripped, but
    do it via an explicit handler so future changes (e.g., rendering as a
    subtitle) only touch this function.
    """
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name in ("short description", "shortdescription"):
            try:
                wikicode.remove(template)
            except ValueError:
                pass


def convert_coord_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{coord|...}} templates with a formatted ``<span class="geo">``."""
    for template in wikicode.filter_templates():
        if str(template.name).strip().lower() != "coord":
            continue
        positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
        formatted = _format_coord(positional)
        try:
            if formatted:
                wikicode.replace(template, f'<span class="geo">{formatted}</span>')
            else:
                wikicode.remove(template)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Date table sorting ({{dts}}, {{date table sorting}})
# ---------------------------------------------------------------------------


def convert_date_sorting_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{dts|year|month|day}} with a formatted date string."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if name not in ("dts", "date table sorting", "dts2"):
            continue
        positional = [str(p.value).strip() for p in template.params if str(p.name).strip().isdigit()]
        if not positional:
            try:
                wikicode.remove(template)
            except ValueError:
                pass
            continue
        year = positional[0]
        if len(positional) >= 2:
            month_raw = positional[1]
            try:
                month_name = MONTH_NAMES[int(month_raw)]
            except (ValueError, IndexError):
                month_name = month_raw
            if len(positional) >= 3:
                replacement = f"{month_name} {positional[2]}, {year}"
            else:
                replacement = f"{month_name} {year}"
        else:
            replacement = year
        try:
            wikicode.replace(template, replacement)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Infobox
# ---------------------------------------------------------------------------


def _is_image_field(field_name: str) -> bool:
    name = field_name.lower().strip().replace("-", "_")
    for prefix in IMAGE_FIELD_PREFIXES:
        if name == prefix or name.startswith(prefix + "_") or name.endswith("_" + prefix):
            return True
    return "caption" in name or name.startswith("alt_") or name.endswith("_alt")


def _render_infobox_value_template(template) -> str | None:
    """Render templates that commonly appear inside infobox cell values.

    Returns the rendered string, or ``None`` to remove the template entirely.
    """
    name = str(template.name).strip().lower()
    params = list(template.params)

    # Date templates: positional 1=year, 2=month, 3=day
    if name in (
        "birth date",
        "birth date and age",
        "birth-date and age",
        "death date",
        "death date and age",
        "death-date and age",
        "start date",
        "start date and age",
        "end date",
        "end date and age",
    ):
        indexed: dict[int, str] = {}
        for p in params:
            pname = str(p.name).strip()
            if pname.isdigit():
                indexed[int(pname)] = str(p.value).strip()
        year = indexed.get(1, "")
        month_raw = indexed.get(2, "")
        day = indexed.get(3, "")
        try:
            month_name = MONTH_NAMES[int(month_raw)] if month_raw else ""
        except (ValueError, IndexError):
            month_name = month_raw
        if year and month_name and day:
            return f"{month_name} {day}, {year}"
        if year and month_name:
            return f"{month_name} {year}"
        return year or None

    # flatlist / plainlist: first positional param holds wiki-list lines
    if name in ("flatlist", "plainlist"):
        for p in params:
            pname = str(p.name).strip()
            if pname in ("class", "style", "indent"):
                continue
            items: list[str] = []
            for line in str(p.value).split("\n"):
                item = line.strip().lstrip("*#").strip()
                if item:
                    item = _render_infobox_value(item)
                if item:
                    items.append(item)
            if items:
                lis = "".join(f"<li>{item}</li>" for item in items)
                return f'<ul class="infobox-list">{lis}</ul>'
            break

    # collapsible list: either a single block-content param (e.g. wrapping a
    # {{Plainlist}}) or multiple positional items. Render recursively so the
    # inner {{Plainlist}} handler runs on the extracted content.
    if name == "collapsible list":
        _CL_NAMED = {"class", "style", "title", "expand", "framestyle", "titlestyle", "liststyle", "bullet"}
        content_params = [
            str(p.value).strip()
            for p in params
            if str(p.name).strip() not in _CL_NAMED and str(p.value).strip()
        ]
        if len(content_params) == 1:
            return _render_infobox_value(content_params[0])
        if content_params:
            rendered_items = [_render_infobox_value(v) for v in content_params]
            rendered_items = [i for i in rendered_items if i]
            if rendered_items:
                lis = "".join(f"<li>{item}</li>" for item in rendered_items)
                return f'<ul class="infobox-list">{lis}</ul>'
        return None

    # ubl / unbulleted list / bulleted list: positional params are items
    if name in ("unbulleted list", "ubl", "bulleted list"):
        items = []
        for p in params:
            pname = str(p.name).strip()
            if pname.isdigit():
                item = _render_infobox_value(str(p.value).strip())
                if item:
                    items.append(item)
        if items:
            lis = "".join(f"<li>{item}</li>" for item in items)
            return f'<ul class="infobox-list">{lis}</ul>'

    # hlist: positional params rendered as "a · b · c"
    if name == "hlist":
        items = []
        for p in params:
            pname = str(p.name).strip()
            if pname in ("class", "style", "ul_style", "li_style", "indent", "item_style"):
                continue
            v = str(p.value).strip()
            if v:
                items.append(v)
        return " · ".join(items) if items else None

    # Language annotation
    if name in ("lang", "langx", "lang-xx"):
        positional = [str(p.value).strip() for p in params if str(p.name).strip().isdigit()]
        if len(positional) >= 2:
            return _render_lang(positional[0], positional[-1])
        return None

    # Pass-through wrappers: render the (last) positional param
    if name in ("nowrap", "abbr", "msd", "nowr"):
        for p in params:
            pname = str(p.name).strip()
            if not pname.isdigit():
                continue
            val = str(p.value).strip()
            if val:
                return val
        if params:
            return str(params[-1].value).strip() or None

    # {{URL|url|label}}
    if name == "url":
        if params:
            url = str(params[0].value).strip()
            label = str(params[1].value).strip() if len(params) > 1 else url
            return (
                f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
                f'target="_blank">{html.escape(label)}</a>'
            )

    # {{wikidata|...}} fetches live property values from Wikidata; we have no
    # API access, so skip the row rather than show a stale or empty value.
    if name == "wikidata":
        return None

    return None  # Strip unknown templates entirely.


def _render_infobox_value(raw_value: str) -> str:
    """Process an infobox field value into HTML, handling nested templates and tags."""
    wikicode = mwparserfromhell.parse(raw_value)

    for template in wikicode.filter_templates():
        rendered = _render_infobox_value_template(template)
        try:
            if rendered is not None:
                wikicode.replace(template, rendered)
            else:
                wikicode.remove(template)
        except ValueError:
            pass

    # Drop refs and unwrap inline-formatting tags (keep their contents).
    for tag in wikicode.filter_tags():
        tag_name = str(tag.tag).strip().lower()
        try:
            if tag_name in ("ref", "references"):
                wikicode.remove(tag)
            elif tag_name in ("small", "sup", "sub", "span", "div"):
                wikicode.replace(tag, str(tag.contents))
        except ValueError:
            pass

    text = str(wikicode).strip()

    # Drop bare [[File:...]] / [[Image:...]] (e.g. inside captions).
    text = re.sub(r"\[\[(File|Image):[^\]]*\]\]", "", text, flags=re.IGNORECASE)

    text = convert_bold_italic(text)
    text = convert_links(text)
    return text.strip()


def convert_infobox_templates(wikicode: mwparserfromhell.wikicode.Wikicode) -> None:
    """Replace {{Infobox ...}} templates with HTML <table class="infobox">."""
    for template in wikicode.filter_templates():
        name = str(template.name).strip()
        name_lower = name.lower()
        is_infobox = name_lower.startswith("infobox")
        is_taxobox = name_lower in TAXONOMY_TEMPLATE_NAMES
        if not is_infobox and not is_taxobox:
            continue

        if is_infobox:
            display_type = name[len("infobox") :].strip()
            if display_type:
                display_type = display_type[0].upper() + display_type[1:]
            skip_field = None
        else:
            display_type = str(template.get("name").value).strip() if template.has("name") else "Species"
            skip_field = "name"

        rows: list[tuple[str, str]] = []
        for param in template.params:
            field_name = str(param.name).strip()
            raw_value = str(param.value).strip()

            if skip_field and field_name.lower() == skip_field:
                continue
            if not raw_value or raw_value.startswith("<!--"):
                continue
            if _is_image_field(field_name):
                continue
            if IMAGE_VALUE_RE.match(raw_value):
                continue

            label = field_name.replace("_", " ").strip()
            if label:
                label = label[0].upper() + label[1:]

            rendered = _render_infobox_value(raw_value)
            if not rendered or not rendered.strip():
                continue

            rows.append((html.escape(label), rendered))

        if not rows and not display_type:
            try:
                wikicode.remove(template)
            except ValueError:
                pass
            continue

        parts = ['<table class="infobox">']
        if display_type:
            parts.append(f"<caption>{html.escape(display_type)}</caption>")
        parts.append("<tbody>")
        for label, value in rows:
            parts.append(f"<tr><th>{label}</th><td>{value}</td></tr>")
        parts.append("</tbody>")
        parts.append("</table>")

        try:
            wikicode.replace(template, "\n".join(parts))
        except ValueError:
            pass
