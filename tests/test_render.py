"""Unit tests for `rag.render.html_to_markdown` (LaTeXML HTML → markdown).

Inline HTML fixtures only — no on-disk reads. Mirrors the style of
`tests/test_chunker_cleaner.py`. Cases ported from
`local_wikipedia/tests/test_arxiv_render.py`; the upstream's
`prepare_local_view` cases are out of scope here (that helper is
frontend-only and wasn't ported).
"""

from rag.render import html_to_markdown


def _wrap(body: str) -> str:
    """Wrap an HTML body fragment in the LaTeXML article structure."""
    return f'<html><body><article class="ltx_document">{body}</article></body></html>'


class TestHeadings:
    def test_h2_becomes_double_hash(self) -> None:
        out = html_to_markdown(_wrap('<h2 class="ltx_title">Introduction</h2>'))
        assert "## Introduction" in out

    def test_h3_becomes_triple_hash(self) -> None:
        out = html_to_markdown(_wrap('<h3 class="ltx_title">Background</h3>'))
        assert "### Background" in out

    def test_section_number_prefix_dropped(self) -> None:
        out = html_to_markdown(
            _wrap('<h2 class="ltx_title"><span class="ltx_tag">1 </span>Introduction</h2>')
        )
        assert "## Introduction" in out
        assert "1 " not in out

    def test_document_title_h1_dropped(self) -> None:
        out = html_to_markdown(
            _wrap('<h1 class="ltx_title ltx_title_document">Paper Title</h1>')
        )
        assert "Paper Title" not in out

    def test_abstract_h6_becomes_h2(self) -> None:
        out = html_to_markdown(
            _wrap('<h6 class="ltx_title ltx_title_abstract">Abstract</h6>')
        )
        assert "## Abstract" in out


class TestParagraphs:
    def test_simple_paragraph(self) -> None:
        out = html_to_markdown(_wrap('<p class="ltx_p">Hello world.</p>'))
        assert "Hello world." in out

    def test_paragraphs_separated_by_blank_line(self) -> None:
        out = html_to_markdown(
            _wrap('<p class="ltx_p">First.</p><p class="ltx_p">Second.</p>')
        )
        assert "First.\n\nSecond." in out

    def test_br_keeps_both_lines(self) -> None:
        out = html_to_markdown(
            _wrap('<p class="ltx_p">line one<br class="ltx_break">line two</p>')
        )
        assert "line one" in out and "line two" in out


class TestMath:
    def test_inline_math_uses_alttext(self) -> None:
        out = html_to_markdown(
            _wrap('<p><math alttext="\\frac{1}{2}" display="inline">…</math></p>')
        )
        assert "$\\frac{1}{2}$" in out

    def test_block_math_uses_double_dollars(self) -> None:
        out = html_to_markdown(
            _wrap('<p><math alttext="\\sum_{i}" display="block">…</math></p>')
        )
        assert "$$\\sum_{i}$$" in out

    def test_math_without_display_defaults_to_inline(self) -> None:
        out = html_to_markdown(_wrap('<p><math alttext="x">x</math></p>'))
        assert "$x$" in out

    def test_math_without_alttext_falls_back_to_text(self) -> None:
        out = html_to_markdown(_wrap('<p><math display="inline">xy</math></p>'))
        assert "$xy$" in out


class TestLists:
    def test_unordered_list(self) -> None:
        out = html_to_markdown(_wrap("<ul><li>one</li><li>two</li></ul>"))
        assert "- one" in out
        assert "- two" in out

    def test_ordered_list(self) -> None:
        out = html_to_markdown(_wrap("<ol><li>alpha</li><li>beta</li></ol>"))
        assert "1. alpha" in out
        assert "2. beta" in out


class TestTables:
    def test_simple_table_to_markdown(self) -> None:
        html = _wrap(
            """
            <table class="ltx_tabular">
              <tr><th>Parameter</th><th>Value</th></tr>
              <tr><td>dim</td><td>4096</td></tr>
              <tr><td>layers</td><td>32</td></tr>
            </table>
            """
        )
        out = html_to_markdown(html)
        assert "| Parameter | Value |" in out
        assert "| --- | --- |" in out
        assert "| dim | 4096 |" in out
        assert "| layers | 32 |" in out

    def test_pipes_in_cells_escaped(self) -> None:
        html = _wrap("<table><tr><th>A</th></tr><tr><td>x|y</td></tr></table>")
        out = html_to_markdown(html)
        assert r"x\|y" in out

    def test_empty_table_produces_no_output(self) -> None:
        html = _wrap("<table></table>")
        out = html_to_markdown(html).strip()
        assert out == ""


class TestFigures:
    def test_caption_emitted_as_italic_line(self) -> None:
        html = _wrap(
            '<figure class="ltx_figure">'
            '<img src="x1.png">'
            "<figcaption>Figure 1: Sliding Window Attention.</figcaption>"
            "</figure>"
        )
        out = html_to_markdown(html)
        assert "*Figure 1: Sliding Window Attention.*" in out

    def test_image_src_not_included(self) -> None:
        """Figures contribute caption text only — image refs live in the local view."""
        html = _wrap(
            '<figure class="ltx_figure">'
            '<img src="x1.png">'
            "<figcaption>Cap.</figcaption>"
            "</figure>"
        )
        out = html_to_markdown(html)
        assert "x1.png" not in out
        assert "*Cap.*" in out


class TestCitationsAndLinks:
    def test_citation_renders_as_bracketed_key(self) -> None:
        html = _wrap(
            '<p class="ltx_p">As shown in '
            '<cite class="ltx_cite"><a class="ltx_ref" href="#bib.bib1">smith2020</a></cite>.</p>'
        )
        out = html_to_markdown(html)
        assert "[smith2020]" in out

    def test_multiple_cites_in_one_citation_joined(self) -> None:
        html = _wrap(
            '<p><cite><a href="#bib.b1">smith2020</a><a href="#bib.b2">jones2021</a></cite></p>'
        )
        out = html_to_markdown(html)
        assert "[smith2020, jones2021]" in out

    def test_external_link_preserved(self) -> None:
        html = _wrap('<p><a href="https://mistral.ai">Mistral</a></p>')
        out = html_to_markdown(html)
        assert "[Mistral](https://mistral.ai)" in out

    def test_internal_anchor_becomes_plain_text(self) -> None:
        html = _wrap('<p>See <a href="#sec2">section 2</a> for details.</p>')
        out = html_to_markdown(html)
        assert "section 2" in out
        assert "[section 2]" not in out


class TestInlineStyling:
    def test_bold_via_ltx_class(self) -> None:
        out = html_to_markdown(
            _wrap('<p><span class="ltx_text ltx_font_bold">strong</span></p>')
        )
        assert "**strong**" in out

    def test_italic_via_ltx_class(self) -> None:
        out = html_to_markdown(
            _wrap('<p><span class="ltx_text ltx_font_italic">emph</span></p>')
        )
        assert "*emph*" in out

    def test_typewriter_becomes_code(self) -> None:
        out = html_to_markdown(
            _wrap('<p><span class="ltx_text ltx_font_typewriter">code</span></p>')
        )
        assert "`code`" in out


class TestStripping:
    def test_strips_nav(self) -> None:
        html = _wrap('<nav class="ltx_TOC">Contents</nav><p>Body.</p>')
        out = html_to_markdown(html)
        assert "Contents" not in out
        assert "Body." in out

    def test_strips_scripts_and_styles(self) -> None:
        html = _wrap("<script>var x=1;</script><style>p{color:red}</style><p>Body.</p>")
        out = html_to_markdown(html)
        assert "var x" not in out
        assert "color:red" not in out
        assert "Body." in out

    def test_strips_authors_block(self) -> None:
        html = _wrap('<div class="ltx_authors">Alice, Bob</div><p>Body.</p>')
        out = html_to_markdown(html)
        assert "Alice" not in out
        assert "Body." in out


class TestCleanup:
    def test_collapses_consecutive_blank_lines(self) -> None:
        out = html_to_markdown(_wrap("<p>One.</p><p></p><p></p><p>Two.</p>"))
        assert "\n\n\n" not in out

    def test_ends_with_single_newline(self) -> None:
        out = html_to_markdown(_wrap("<p>Body.</p>"))
        assert out.endswith("\n")
        assert not out.endswith("\n\n\n")
