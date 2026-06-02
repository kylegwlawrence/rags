"""Presentation-MathML → LaTeX converter (stdlib only).

OpenStax CNXML embeds every formula as *presentation* MathML (`<m:math>` with
`<m:mfrac>`, `<m:msup>`, …) and carries **no** TeX annotation, so the equations
have to be rebuilt as LaTeX from the presentation tree. Rather than add a
MathML dependency, this walks the parsed element tree with `xml.etree` (already
used to parse the CNXML itself) and emits LaTeX for the bounded set of tags the
OpenStax corpus actually uses.

The single entry point is `mathml_to_latex(elem)`, which takes a parsed
`<math>` element (namespaces already present on the tags) and returns a LaTeX
string *without* `$` delimiters — the caller wraps it inline (`$…$`) or display
(`$$…$$`). Anything unrecognised falls back to its concatenated text content, so
an unusual construct degrades to readable characters rather than vanishing.
"""

from xml.etree.ElementTree import Element

# Unicode operators/identifiers OpenStax emits inside <m:mo>/<m:mi>, mapped to
# their LaTeX commands. Characters not listed pass through unchanged (digits,
# ASCII letters, `+ - = ( ) [ ]`, etc. are already valid LaTeX).
_SYMBOLS = {
    "−": "-", "–": "-", "—": "-",          # various unicode minus/dashes
    "×": r"\times ", "⋅": r"\cdot ", "·": r"\cdot ", "∗": "*",
    "÷": r"\div ", "±": r"\pm ", "∓": r"\mp ",
    "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "≈": r"\approx ",
    "≡": r"\equiv ", "∼": r"\sim ", "≅": r"\cong ", "∝": r"\propto ",
    "→": r"\to ", "←": r"\leftarrow ", "⇒": r"\Rightarrow ",
    "⇔": r"\Leftrightarrow ", "↦": r"\mapsto ",
    "∞": r"\infty ", "∂": r"\partial ", "∇": r"\nabla ",
    "∫": r"\int ", "∑": r"\sum ", "∏": r"\prod ", "√": r"\sqrt",
    "∈": r"\in ", "∉": r"\notin ", "⊂": r"\subset ", "⊆": r"\subseteq ",
    "∪": r"\cup ", "∩": r"\cap ", "∅": r"\emptyset ", "∖": r"\setminus ",
    "∀": r"\forall ", "∃": r"\exists ", "¬": r"\neg ", "∧": r"\wedge ",
    "∨": r"\vee ", "∘": r"\circ ", "⋯": r"\cdots ", "…": r"\dots ",
    "°": r"^\circ ", "′": "'", "″": "''",
    # Greek lower-case
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
    "ε": r"\varepsilon ", "ζ": r"\zeta ", "η": r"\eta ", "θ": r"\theta ",
    "ι": r"\iota ", "κ": r"\kappa ", "λ": r"\lambda ", "μ": r"\mu ",
    "ν": r"\nu ", "ξ": r"\xi ", "π": r"\pi ", "ρ": r"\rho ",
    "σ": r"\sigma ", "τ": r"\tau ", "υ": r"\upsilon ", "φ": r"\varphi ",
    "χ": r"\chi ", "ψ": r"\psi ", "ω": r"\omega ",
    # Greek upper-case
    "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ", "Λ": r"\Lambda ",
    "Ξ": r"\Xi ", "Π": r"\Pi ", "Σ": r"\Sigma ", "Φ": r"\Phi ",
    "Ψ": r"\Psi ", "Ω": r"\Omega ",
}


# Accent operators that may sit in the over/under slot of <mover>/<munder>,
# mapped to their LaTeX accent command (used in place of \overset/\underset,
# which would emit a bare `^`/`~` and fail to parse). Both the spacing-modifier
# letters and the combining-mark codepoints OpenStax uses are covered.
_ACCENT_OVER = {
    "^": r"\hat", "ˆ": r"\hat", "̂": r"\hat",
    "~": r"\tilde", "˜": r"\tilde", "̃": r"\tilde",
    "¯": r"\bar", "‾": r"\bar", "̄": r"\bar",
    "→": r"\vec", "⃗": r"\vec", "⇀": r"\vec",
    "˙": r"\dot", "̇": r"\dot",
    "¨": r"\ddot", "̈": r"\ddot",
    "ˇ": r"\check", "̌": r"\check",
    "˘": r"\breve", "̆": r"\breve",
    "´": r"\acute", "́": r"\acute",
    "`": r"\grave", "̀": r"\grave",
}
_ACCENT_UNDER = {
    "_": r"\underline", "̲": r"\underline", "‾": r"\underline",
}
# Fallback keyed on the *rendered* overscript: a bare `^`/`~` is invalid LaTeX
# on its own (`\overset{^}{y}` fails to parse), so any overscript that reduces
# to one — however the source nested it — becomes the corresponding accent.
_BARE_OVER_ACCENT = {"^": r"\hat", "~": r"\tilde"}

# Fence delimiters for \left…\right. Braces must be escaped; an empty delimiter
# becomes `.` (no delimiter). Anything else passes through (parens, brackets,
# bars are valid as-is).
_DELIMS = {"{": r"\{", "}": r"\}", "": ".", "‖": r"\|", "|": "|",
           "⟨": r"\langle", "⟩": r"\rangle", "⌊": r"\lfloor", "⌋": r"\rfloor",
           "⌈": r"\lceil", "⌉": r"\rceil"}


def _local(tag: str) -> str:
    """Strip any `{namespace}` prefix, returning the bare element name."""
    return tag.rsplit("}", 1)[-1]


def _delim(ch: str) -> str:
    """Map a MathML fence character to a LaTeX `\\left`/`\\right` delimiter."""
    ch = (ch or "").strip()
    return _DELIMS.get(ch, ch or ".")


def _accent_cmd(mark: "Element", table: dict) -> str | None:
    """Return the LaTeX accent command for an over/under mark, or None.

    Only a leaf operator/identifier whose text is a known accent counts; a
    `<munder>` whose mark is a limit expression (e.g. `x\\to 3`) is not an
    accent and falls through to `\\underset`.
    """
    # Unwrap a single-child container (the accent operator is sometimes wrapped
    # in an <mrow>, e.g. <mover><mi>y</mi><mrow><mo>^</mo></mrow></mover>).
    while mark is not None and _local(mark.tag) in ("mrow", "mstyle", "mpadded"):
        kids = list(mark)
        if len(kids) != 1:
            break
        mark = kids[0]
    if mark is None or _local(mark.tag) not in ("mo", "mi", "mtext"):
        return None
    return table.get((mark.text or "").strip())


# LaTeX-special characters that may appear *literally* inside a leaf token and
# must be backslash-escaped so they render as themselves instead of being
# interpreted as syntax. Braces are the important case: OpenStax emits the
# curly brace of a piecewise function as a literal `<mo>{</mo>`, and an
# unescaped `{` opens an unclosed group that makes the whole formula fail to
# render. Structural braces (grouping in `\frac{…}`, `…^{…}`) and subscripts
# (`…_{…}`) are produced by `_convert` directly, never routed through here, so
# escaping a `_`/`{`/`\`/`&` that *does* reach a token is safe — it is
# genuinely literal (e.g. the `_` in a fill-in-the-blank "14___6", or a stray
# `&entity;` that leaked from the source). A structural cell separator `&` and a
# bare accent `^` are produced by `_convert` directly, never routed through
# here.
_LATEX_ESCAPE = {
    "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "$": r"\$",
    "_": r"\_", "\\": r"\backslash ", "&": r"\&",
}

# Combining marks that LaTeX/KaTeX can't take as a trailing accent and that
# carry no essential meaning when flattened — dropped so the base character
# still renders. U+0337/U+0338 are the "cancel" overlays OpenStax overlays on a
# factor being struck out while reducing a fraction.
_STRIP_CHARS = {"̷", "̸"}


def _map_token(text: str) -> str:
    """Translate a leaf token (mi/mn/mo/mtext content) char-by-char to LaTeX."""
    out: list[str] = []
    for ch in text or "":
        if ch in _STRIP_CHARS:
            continue
        out.append(_SYMBOLS.get(ch) or _LATEX_ESCAPE.get(ch) or ch)
    return "".join(out)


def _children(elem: Element) -> list[Element]:
    return list(elem)


def _convert_children(kids: list[Element]) -> str:
    r"""Concatenate a container's children, recognising piecewise functions.

    OpenStax writes a piecewise definition as an `<mo>{</mo>` brace fence
    immediately followed by an `<mtable>` of cases. Rendered literally the brace
    is just a small `\{`; paired here it becomes a tall `\left\{ … \right.`
    spanning the whole table — the conventional piecewise look. A trailing
    `<mo>}</mo>` after a table is paired symmetrically. Everything else is
    converted child-by-child as before.
    """
    parts: list[str] = []
    i = 0
    while i < len(kids):
        cur = kids[i]
        nxt = kids[i + 1] if i + 1 < len(kids) else None
        cur_text = (cur.text or "").strip() if _local(cur.tag) == "mo" else None
        if cur_text == "{" and nxt is not None and _local(nxt.tag) == "mtable":
            parts.append(rf"\left\{{ {_convert(nxt)} \right.")
            i += 2
            continue
        if cur_text == "}" and parts and _local(cur.tag) == "mo":
            # A closing brace right after a table: turn the table we just emitted
            # into a right-delimited group. Rare; handled defensively.
            parts.append(r"\}")
            i += 1
            continue
        parts.append(_convert(cur))
        i += 1
    return "".join(parts)


def _convert(elem: Element) -> str:
    """Recursively convert one MathML element to a LaTeX fragment."""
    tag = _local(elem.tag)

    # Leaf tokens: the text is the content.
    if tag in ("mi", "mn", "mo", "mtext", "ms"):
        return _map_token((elem.text or "").strip())
    if tag in ("mspace", "none"):
        return " "

    kids = _children(elem)

    # Containers: concatenate children, pairing a brace fence with a following
    # table into a proper tall `\left\{ … \right.` (piecewise functions).
    if tag in ("math", "mrow", "mstyle", "mpadded", "merror", "semantics"):
        return _convert_children(kids)

    if tag == "mfrac" and len(kids) == 2:
        return rf"\frac{{{_convert(kids[0])}}}{{{_convert(kids[1])}}}"
    if tag == "msup" and len(kids) == 2:
        return rf"{{{_convert(kids[0])}}}^{{{_convert(kids[1])}}}"
    if tag == "msub" and len(kids) == 2:
        return rf"{{{_convert(kids[0])}}}_{{{_convert(kids[1])}}}"
    if tag == "msubsup" and len(kids) == 3:
        return (
            rf"{{{_convert(kids[0])}}}_{{{_convert(kids[1])}}}"
            rf"^{{{_convert(kids[2])}}}"
        )
    if tag == "msqrt":
        return rf"\sqrt{{{''.join(_convert(c) for c in kids)}}}"
    if tag == "mroot" and len(kids) == 2:
        return rf"\sqrt[{_convert(kids[1])}]{{{_convert(kids[0])}}}"
    if tag == "mover" and len(kids) == 2:
        over = _convert(kids[1])
        cmd = _accent_cmd(kids[1], _ACCENT_OVER) or _BARE_OVER_ACCENT.get(over.strip())
        if cmd:  # an accent operator (hat/bar/vec/…) over its base
            return rf"{cmd}{{{_convert(kids[0])}}}"
        return rf"\overset{{{over}}}{{{_convert(kids[0])}}}"
    if tag == "munder" and len(kids) == 2:
        cmd = _accent_cmd(kids[1], _ACCENT_UNDER)
        if cmd:
            return rf"{cmd}{{{_convert(kids[0])}}}"
        return rf"\underset{{{_convert(kids[1])}}}{{{_convert(kids[0])}}}"
    if tag == "munderover" and len(kids) == 3:
        return (
            rf"\underset{{{_convert(kids[1])}}}{{\overset{{{_convert(kids[2])}}}"
            rf"{{{_convert(kids[0])}}}}}"
        )
    if tag == "mfenced":
        # Deprecated but simple: wrap children in the given (default parens)
        # separators. Render with stretchy \left…\right so a `{` opener (and an
        # empty closer, common in systems of equations) produces a valid, tall
        # delimiter pair instead of a bare unbalanced brace.
        sep = elem.get("separators", ",")
        inner = (sep + " ").join(_convert(c) for c in kids)
        opener = _delim(elem.get("open", "("))
        closer = _delim(elem.get("close", ")"))
        return rf"\left{opener} {inner} \right{closer}"

    # Tables/matrices: render rows separated by `\\`, cells by `&`.
    if tag == "mtable":
        rows = [_convert(r) for r in kids if _local(r.tag) == "mtr"]
        body = r" \\ ".join(rows)
        return rf"\begin{{matrix}} {body} \end{{matrix}}"
    if tag == "mtr":
        return " & ".join(
            _convert(c) for c in kids if _local(c.tag) in ("mtd", "mtr")
        )
    if tag == "mtd":
        return "".join(_convert(c) for c in kids)

    # Unknown construct: fall back to concatenated child/text content so the
    # math degrades to readable characters instead of disappearing.
    if kids:
        return "".join(_convert(c) for c in kids)
    return _map_token((elem.text or "").strip())


def mathml_to_latex(elem: Element) -> str:
    """Convert a parsed `<math>` element to a LaTeX string (no `$` delimiters).

    Args:
        elem: An `xml.etree` element for a MathML `<math>` node (or any MathML
            subtree). Namespaces on the tags are tolerated.

    Returns:
        A LaTeX fragment with internal whitespace collapsed. Empty string when
        the element has no convertible content.
    """
    latex = _convert(elem)
    # Collapse the thin-space padding the symbol map leaves behind.
    return " ".join(latex.split())
