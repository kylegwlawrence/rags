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


def _local(tag: str) -> str:
    """Strip any `{namespace}` prefix, returning the bare element name."""
    return tag.rsplit("}", 1)[-1]


def _map_token(text: str) -> str:
    """Translate a leaf token (mi/mn/mo/mtext content) char-by-char to LaTeX."""
    if not text:
        return ""
    return "".join(_SYMBOLS.get(ch, ch) for ch in text)


def _children(elem: Element) -> list[Element]:
    return list(elem)


def _convert(elem: Element) -> str:
    """Recursively convert one MathML element to a LaTeX fragment."""
    tag = _local(elem.tag)

    # Leaf tokens: the text is the content.
    if tag in ("mi", "mn", "mo", "mtext", "ms"):
        return _map_token((elem.text or "").strip())
    if tag in ("mspace", "none"):
        return " "

    kids = _children(elem)

    # Containers: concatenate children (a thin space keeps adjacent tokens from
    # fusing, e.g. two <mi> running together).
    if tag in ("math", "mrow", "mstyle", "mpadded", "merror", "semantics"):
        return "".join(_convert(c) for c in kids)

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
        return rf"\overset{{{_convert(kids[1])}}}{{{_convert(kids[0])}}}"
    if tag == "munder" and len(kids) == 2:
        return rf"\underset{{{_convert(kids[1])}}}{{{_convert(kids[0])}}}"
    if tag == "munderover" and len(kids) == 3:
        return (
            rf"\underset{{{_convert(kids[1])}}}{{\overset{{{_convert(kids[2])}}}"
            rf"{{{_convert(kids[0])}}}}}"
        )
    if tag == "mfenced":
        # Deprecated but simple: wrap children in the given (default parens)
        # separators. open/close attrs give the brackets.
        opener = elem.get("open", "(")
        closer = elem.get("close", ")")
        sep = elem.get("separators", ",")
        inner = (sep + " ").join(_convert(c) for c in kids)
        return f"{opener}{inner}{closer}"

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
