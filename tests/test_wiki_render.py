"""Tests for rag.wiki_render (wikitext → HTML converter), ported from
local_wikipedia's render package. Link assertions are adapted to this repo's
in-app wikilink shape (class="wikilink" data-wiki-title=...)."""

from rag.wiki_render import (
    _clean_extra_markup,
    _convert_bold_italic,
    _convert_headings,
    _convert_links,
    _convert_lists,
    _convert_tables,
    _extract_math_tags,
    _parse_cell,
    _restore_math_tags,
    convert_wikitext_to_html,
)


class TestConvertBoldItalic:
    def test_bold_conversion(self) -> None:
        text = "This is '''bold''' text"
        result = _convert_bold_italic(text)
        assert result == "This is <strong>bold</strong> text"

    def test_italic_conversion(self) -> None:
        text = "This is ''italic'' text"
        result = _convert_bold_italic(text)
        assert result == "This is <em>italic</em> text"

    def test_bold_italic_conversion(self) -> None:
        text = "This is '''''bold and italic''''' text"
        result = _convert_bold_italic(text)
        assert result == "This is <strong><em>bold and italic</em></strong> text"

    def test_mixed_formatting(self) -> None:
        text = "'''Bold''' and ''italic'' and '''''both'''''"
        result = _convert_bold_italic(text)
        assert result == "<strong>Bold</strong> and <em>italic</em> and <strong><em>both</em></strong>"

    def test_multiple_bold_sections(self) -> None:
        text = "'''First''' and '''second''' bold"
        result = _convert_bold_italic(text)
        assert result == "<strong>First</strong> and <strong>second</strong> bold"


class TestConvertHeadings:
    def test_level_2_heading(self) -> None:
        text = "== Heading =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heading">Heading</h2>'

    def test_level_3_heading(self) -> None:
        text = "=== Subheading ==="
        result = _convert_headings(text)
        assert result == '<h3 id="Subheading">Subheading</h3>'

    def test_level_4_heading(self) -> None:
        text = "==== Sub-subheading ===="
        result = _convert_headings(text)
        assert result == '<h4 id="Sub-subheading">Sub-subheading</h4>'

    def test_multiple_headings(self) -> None:
        text = "== First ==\nSome text\n=== Second ==="
        result = _convert_headings(text)
        assert '<h2 id="First">First</h2>' in result
        assert '<h3 id="Second">Second</h3>' in result

    def test_heading_with_whitespace(self) -> None:
        text = "==  Heading  =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heading">Heading</h2>'

    def test_heading_with_spaces_in_text(self) -> None:
        text = "== Heat transfer =="
        result = _convert_headings(text)
        assert result == '<h2 id="Heat_transfer">Heat transfer</h2>'


class TestConvertLinks:
    def test_simple_link(self) -> None:
        text = "See [[Python]]"
        result = _convert_links(text)
        assert 'class="wikilink"' in result
        assert 'data-wiki-title="Python"' in result
        assert ">Python</a>" in result

    def test_link_has_no_htmx_attributes(self) -> None:
        # Internal article links carry no htmx attributes — navigation is
        # handled in-app by a click delegate that reads data-wiki-title.
        text = "See [[Python]]"
        result = _convert_links(text)
        assert "hx-get" not in result
        assert "hx-target" not in result

    def test_link_with_label(self) -> None:
        text = "See [[Python (programming language)|Python]]"
        result = _convert_links(text)
        assert 'data-wiki-title="Python (programming language)"' in result
        assert ">Python</a>" in result

    def test_link_with_spaces(self) -> None:
        text = "[[United States]]"
        result = _convert_links(text)
        assert 'data-wiki-title="United States"' in result
        assert ">United States</a>" in result

    def test_multiple_links(self) -> None:
        text = "[[First]] and [[Second]]"
        result = _convert_links(text)
        assert 'data-wiki-title="First"' in result
        assert 'data-wiki-title="Second"' in result

    def test_link_in_sentence(self) -> None:
        text = "Programming in [[Python]] is fun"
        result = _convert_links(text)
        assert 'data-wiki-title="Python"' in result
        assert "Programming in " in result
        assert "</a> is fun" in result

    def test_lowercase_first_letter_capitalised(self) -> None:
        # MediaWiki capitalises the first letter of every wikilink target.
        text = "[[python]]"
        result = _convert_links(text)
        assert 'data-wiki-title="Python"' in result
        # The visible label keeps the original casing the author wrote.
        assert ">python</a>" in result

    def test_anchor_split_into_url_fragment(self) -> None:
        # [[Foo#Bar]] should look up "Foo" but keep "#Bar" as the URL fragment.
        text = "[[Python#History]]"
        result = _convert_links(text)
        assert 'data-wiki-title="Python"' in result
        assert 'data-wiki-anchor="History"' in result

    def test_label_can_contain_inline_code(self) -> None:
        # Labels are not HTML-escaped so inline tags survive.
        text = "[[Python|<code>print()</code>]]"
        result = _convert_links(text)
        assert "<code>print()</code></a>" in result

    def test_file_link_stripped(self) -> None:
        result = _convert_links("[[File:Map.jpg|thumb|A caption]]")
        assert "thumb" not in result
        assert "File:" not in result

    def test_image_link_stripped(self) -> None:
        result = _convert_links("[[Image:Test.png|center|200px]]")
        assert "Image:" not in result

    def test_media_link_stripped(self) -> None:
        result = _convert_links("[[Media:Audio.ogg|Listen]]")
        assert "Media:" not in result

    def test_category_link_stripped(self) -> None:
        result = _convert_links("[[Category:Maps]]")
        assert "Category:" not in result

    def test_pipe_trick_strips_parenthetical(self) -> None:
        result = _convert_links("[[Mercury (planet)|]]")
        assert 'data-wiki-title="Mercury (planet)"' in result
        assert ">Mercury</a>" in result

    def test_pipe_trick_strips_namespace(self) -> None:
        result = _convert_links("[[Wikipedia:Foo|]]")
        assert 'data-wiki-title="Wikipedia:Foo"' in result
        assert ">Foo</a>" in result

    def test_pipe_trick_strips_comma_clause(self) -> None:
        result = _convert_links("[[Smith, John|]]")
        assert 'data-wiki-title="Smith, John"' in result
        assert ">Smith</a>" in result

    def test_linktrail_simple(self) -> None:
        result = _convert_links("[[Apple]]s are red")
        assert 'data-wiki-title="Apple"' in result
        assert ">Apples</a>" in result

    def test_linktrail_with_piped_label(self) -> None:
        result = _convert_links("[[Apple|apple]]s are red")
        assert ">apples</a>" in result

    def test_no_linktrail_when_uppercase_follows(self) -> None:
        # Only lowercase trail is absorbed — uppercase starts a new word.
        result = _convert_links("[[Apple]]Sentence start")
        assert ">Apple</a>" in result
        assert "Sentence start" in result


class TestConvertLists:
    def test_bullet_list(self) -> None:
        text = "* Item 1\n* Item 2"
        result = _convert_lists(text)
        assert "<ul>" in result
        assert "<li>Item 1</li>" in result
        assert "<li>Item 2</li>" in result
        assert "</ul>" in result

    def test_numbered_list(self) -> None:
        text = "# First\n# Second"
        result = _convert_lists(text)
        assert "<ol>" in result
        assert "<li>First</li>" in result
        assert "<li>Second</li>" in result
        assert "</ol>" in result

    def test_nested_bullet_list(self) -> None:
        text = "* Level 1\n** Level 2\n*** Level 3"
        result = _convert_lists(text)
        assert result.count("<ul>") == 3
        assert result.count("</ul>") == 3
        assert "<li>Level 1</li>" in result
        assert "<li>Level 2</li>" in result
        assert "<li>Level 3</li>" in result

    def test_nested_numbered_list(self) -> None:
        text = "# Level 1\n## Level 2\n### Level 3"
        result = _convert_lists(text)
        assert result.count("<ol>") == 3
        assert result.count("</ol>") == 3
        assert "<li>Level 1</li>" in result
        assert "<li>Level 2</li>" in result
        assert "<li>Level 3</li>" in result

    def test_definition_term(self) -> None:
        text = "; Python"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dt>Python</dt>" in result
        assert "</dl>" in result

    def test_definition_description(self) -> None:
        text = ": A programming language"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dd>A programming language</dd>" in result
        assert "</dl>" in result

    def test_deeper_indentation(self) -> None:
        text = ":: Further indented"
        result = _convert_lists(text)
        # Two colons create nested definition lists
        assert result.count("<dl>") >= 1
        assert "<dd>Further indented</dd>" in result

    def test_definition_list(self) -> None:
        text = "; Term\n: Description"
        result = _convert_lists(text)
        assert "<dl>" in result
        assert "<dt>Term</dt>" in result
        assert "<dd>Description</dd>" in result
        assert "</dl>" in result

    def test_mixed_ordered_then_bullet(self) -> None:
        text = "#* Sub-bullet under numbered"
        result = _convert_lists(text)
        assert "<ol>" in result
        assert "<ul>" in result
        assert "<li>Sub-bullet under numbered</li>" in result

    def test_mixed_bullet_then_ordered(self) -> None:
        text = "*# Sub-number under bullet"
        result = _convert_lists(text)
        assert "<ul>" in result
        assert "<ol>" in result
        assert "<li>Sub-number under bullet</li>" in result

    def test_mixed_content(self) -> None:
        text = "Normal text\n* List item\nMore text"
        result = _convert_lists(text)
        assert "Normal text" in result
        assert "<ul>" in result
        assert "<li>List item</li>" in result
        assert "More text" in result


class TestParseCell:
    def test_plain_content(self) -> None:
        result = _parse_cell(" value ")
        assert result["content"] == "value"
        assert result["align"] is None

    def test_parses_style_attribute(self) -> None:
        result = _parse_cell('style="text-align:center" | 42')
        assert result["content"] == "42"
        assert result["align"] == "center"

    def test_parses_colspan_attribute(self) -> None:
        result = _parse_cell("colspan=2 | text")
        assert result["content"] == "text"
        assert result["colspan"] == 2

    def test_parses_rowspan_attribute(self) -> None:
        result = _parse_cell("rowspan=3 | text")
        assert result["content"] == "text"
        assert result["rowspan"] == 3

    def test_preserves_wikilink_with_label(self) -> None:
        # The | inside [[...]] must not be treated as an attribute separator
        result = _parse_cell("[[Python (programming language)|Python]]")
        assert "Python" in result["content"]

    def test_parses_align_attribute(self) -> None:
        result = _parse_cell('align="center" | content')
        assert result["content"] == "content"
        assert result["align"] == "center"

    def test_parses_background_style(self) -> None:
        result = _parse_cell('style="background:#eee" | content')
        assert result["content"] == "content"
        assert result["style"] == "background:#eee"


class TestConvertTables:
    def test_basic_table_with_headers(self) -> None:
        wikitext = '{| class="wikitable"\n|-\n! Name !! Age\n|-\n| Alice || 30\n|-\n| Bob || 25\n|}'
        result = _convert_tables(wikitext)
        assert "<table" in result
        assert "<thead>" in result
        assert "<th>Name</th>" in result
        assert "<th>Age</th>" in result
        assert "<tbody>" in result
        assert "<td>Alice</td>" in result
        assert "<td>30</td>" in result
        assert "<td>Bob</td>" in result
        assert "<td>25</td>" in result
        assert "</table>" in result

    def test_table_without_explicit_headers(self) -> None:
        wikitext = "{|\n|-\n| A || B\n|-\n| C || D\n|}"
        result = _convert_tables(wikitext)
        # First data row becomes the header
        assert "<thead>" in result
        assert "<th>A</th>" in result
        assert "<th>B</th>" in result
        assert "<tbody>" in result
        assert "<td>C</td>" in result
        assert "<td>D</td>" in result

    def test_caption_is_preserved(self) -> None:
        wikitext = "{|\n|+ My Caption\n|-\n! H1\n|-\n| D1\n|}"
        result = _convert_tables(wikitext)
        assert "<caption>My Caption</caption>" in result
        assert "<th>H1</th>" in result

    def test_cell_attributes_parsed(self) -> None:
        wikitext = '{|\n|-\n! style="width:50%" | Name\n|-\n| align="center" | Alice\n|}'
        result = _convert_tables(wikitext)
        assert "Name" in result
        assert "Alice" in result
        assert 'class="align-center"' in result

    def test_colspan_attribute(self) -> None:
        wikitext = "{|\n|-\n! colspan=2 | Header\n|-\n| A || B\n|}"
        result = _convert_tables(wikitext)
        assert 'colspan="2"' in result
        assert "<th" in result

    def test_cells_on_separate_lines(self) -> None:
        wikitext = "{|\n|-\n! Header 1 !! Header 2\n|-\n| Cell 1\n| Cell 2\n| Cell 3\n|}"
        result = _convert_tables(wikitext)
        assert "<td>Cell 1</td>" in result
        assert "<td>Cell 2</td>" in result
        assert "<td>Cell 3</td>" in result

    def test_multiple_tables(self) -> None:
        wikitext = "{|\n|-\n! A\n|-\n| 1\n|}\nSome text\n{|\n|-\n! B\n|-\n| 2\n|}"
        result = _convert_tables(wikitext)
        assert "<th>A</th>" in result
        assert "<th>B</th>" in result
        assert "Some text" in result

    def test_unclosed_table_does_not_eat_subsequent_content(self) -> None:
        wikitext = "{| class='wikitable'\n| Cell\n* List item after unclosed table\n"
        result = _convert_tables(wikitext)
        assert "* List item after unclosed table" in result

    def test_empty_table_returns_empty(self) -> None:
        result = _convert_tables("{|\n|}")
        assert result.strip() == ""

    def test_non_table_text_unchanged(self) -> None:
        text = "Normal paragraph\nwith two lines"
        assert _convert_tables(text) == text

    def test_colon_prefixed_table_is_converted(self) -> None:
        wikitext = ':{| class="wikitable"\n|-\n! Name !! Value\n|-\n| Foo || Bar\n|}'
        result = _convert_tables(wikitext)
        assert "<th>Name</th>" in result
        assert "<th>Value</th>" in result
        assert "<td>Foo</td>" in result
        assert "<td>Bar</td>" in result

    def test_full_conversion_renders_table_links(self) -> None:
        wikitext = (
            '{| class="wikitable"\n'
            "|-\n"
            "! Language !! Creator\n"
            "|-\n"
            "| [[Python (programming language)|Python]] || [[Guido van Rossum]]\n"
            "|}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'data-wiki-title="Python (programming language)"' in result
        assert ">Python</a>" in result
        assert 'data-wiki-title="Guido van Rossum"' in result
        assert ">Guido van Rossum</a>" in result

    def test_full_conversion_renders_table_bold(self) -> None:
        wikitext = "{|\n|-\n| '''bold cell''' || normal cell\n|}"
        result = convert_wikitext_to_html(wikitext)
        assert "<strong>bold cell</strong>" in result


class TestCleanExtraMarkup:
    def test_remove_multiple_blank_lines(self) -> None:
        text = "Line 1\n\n\n\nLine 2"
        result = _clean_extra_markup(text)
        assert result == "Line 1\n\nLine 2"

    def test_remove_trailing_whitespace(self) -> None:
        text = "Line with trailing spaces   \nAnother line  "
        result = _clean_extra_markup(text)
        assert result == "Line with trailing spaces\nAnother line"


class TestFullConversion:
    def test_simple_article(self) -> None:
        wikitext = """'''Python''' is a programming language.

== History ==
Python was created in the 1990s.

== Features ==
* Easy to learn
* Powerful
* [[Object-oriented programming|Object-oriented]]
"""
        result = convert_wikitext_to_html(wikitext)

        assert "<p><strong>Python</strong> is a programming language.</p>" in result
        assert '<h2 id="History">History</h2>' in result
        assert '<h2 id="Features">Features</h2>' in result
        assert "<ul>" in result
        assert "<li>Easy to learn</li>" in result
        assert "<li>Powerful</li>" in result
        assert 'data-wiki-title="Object-oriented programming"' in result
        assert ">Object-oriented</a>" in result

    def test_complex_formatting(self) -> None:
        wikitext = """'''''Python''''' is both '''powerful''' and ''easy''.

=== Syntax ===
The syntax is clean.

See also:
* [[Programming language]]
* [[Guido van Rossum]]
"""
        result = convert_wikitext_to_html(wikitext)

        assert "<strong><em>Python</em></strong>" in result
        assert "<strong>powerful</strong>" in result
        assert "<em>easy</em>" in result
        assert '<h3 id="Syntax">Syntax</h3>' in result
        assert 'data-wiki-title="Programming language"' in result
        assert ">Programming language</a>" in result

    def test_empty_text(self) -> None:
        result = convert_wikitext_to_html("")
        assert result == ""

    def test_whitespace_only(self) -> None:
        result = convert_wikitext_to_html("   \n  \n   ")
        assert result == ""

    def test_plain_text(self) -> None:
        wikitext = "This is just plain text with no formatting."
        result = convert_wikitext_to_html(wikitext)
        assert "<p>This is just plain text with no formatting.</p>" in result

    def test_with_templates_removed(self) -> None:
        wikitext = "'''Article''' {{cite web|url=http://example.com}} text"
        result = convert_wikitext_to_html(wikitext)

        assert "<strong>Article</strong>" in result
        assert "text" in result
        assert "cite web" not in result
        assert "{{" not in result

    def test_with_references_removed(self) -> None:
        wikitext = "Text<ref>Citation here</ref> more text"
        result = convert_wikitext_to_html(wikitext)

        assert "Text" in result
        assert "more text" in result
        assert "<ref>" not in result
        assert "Citation" not in result

    def test_with_comments_removed(self) -> None:
        wikitext = "Text <!-- comment --> more text"
        result = convert_wikitext_to_html(wikitext)

        assert "Text" in result
        assert "more text" in result
        assert "<!--" not in result
        assert "comment" not in result

    def test_list_items_with_inline_code(self) -> None:
        wikitext = (
            "===Statements and control flow===\n"
            "Python's [[statement (computer science)|statements]] include the following:\n"
            "* The [[Assignment (computer science)|assignment]] statement, "
            "using a single equals sign <code>=</code>\n"
            "* The <code>[[if-then-else|if]]</code> statement\n"
            "* The <code>[[Foreach#Python|for]]</code> statement\n"
        )
        result = convert_wikitext_to_html(wikitext)
        # Count list items
        assert result.count("<li>") == 3
        assert "<ul>" in result

    def test_malformed_wikitext_graceful_fallback(self) -> None:
        # Test with intentionally broken wikitext that might cause parsing errors
        wikitext = "'''unclosed bold"
        result = convert_wikitext_to_html(wikitext)

        # Should return something, even if it's the original text
        assert result is not None
        assert len(result) > 0

    def test_real_article_structure(self) -> None:
        wikitext = """'''Art''' is a creative activity.

== Types of art ==
There are many types:
* [[Painting]]
* [[Sculpture]]
* [[Music]]

=== Visual art ===
Visual art includes painting and sculpture.

== History ==
Art has existed since ancient times. See [[History of art]].
"""
        result = convert_wikitext_to_html(wikitext)

        # Check structure is preserved
        assert "<strong>Art</strong> is a creative activity" in result
        assert '<h2 id="Types_of_art">Types of art</h2>' in result
        assert '<h3 id="Visual_art">Visual art</h3>' in result
        assert '<h2 id="History">History</h2>' in result

        # Check lists converted
        assert 'data-wiki-title="Painting"' in result
        assert ">Painting</a>" in result
        assert 'data-wiki-title="Sculpture"' in result
        assert ">Sculpture</a>" in result
        assert "<ul>" in result

        # Check links converted
        assert 'data-wiki-title="History of art"' in result
        assert ">History of art</a>" in result

    def test_file_link_with_nested_caption_not_rendered(self) -> None:
        # File links whose captions contain nested wikilinks must be fully
        # stripped — the plain-text regex can't match across nested brackets.
        wikitext = "[[File:Tabula_Rogeriana.jpg|thumb|upright=1.35|center|Caption with [[nested link]] here]]"
        result = convert_wikitext_to_html(wikitext)
        assert "thumb" not in result
        assert "upright" not in result
        assert "File:" not in result

    def test_image_link_with_plain_caption_stripped(self) -> None:
        wikitext = "Some text.\n\n[[Image:Test.png|400px|A plain caption]]\n\nMore text."
        result = convert_wikitext_to_html(wikitext)
        assert "Image:" not in result
        assert "400px" not in result
        assert "Some text" in result
        assert "More text" in result


# ---------------------------------------------------------------------------
# Math rendering
# ---------------------------------------------------------------------------


class TestMathRendering:
    def test_inline_math_becomes_katex_delimiter(self) -> None:
        result = convert_wikitext_to_html("The value <math>x^2</math> is positive.")
        assert "\\(x^2\\)" in result

    def test_block_math_becomes_display_delimiter(self) -> None:
        result = convert_wikitext_to_html('<math display="block">Z = \\frac{x}{y}</math>')
        assert "$$" in result
        assert "Z = \\frac{x}{y}" in result
        assert 'class="math-display"' in result

    def test_block_math_display_single_quotes(self) -> None:
        result = convert_wikitext_to_html("<math display='block'>\\sigma</math>")
        assert "$$" in result
        assert "\\sigma" in result

    def test_z_test_se_formula(self) -> None:
        formula = r"\mathrm{SE} = \frac{\sigma}{\sqrt n} = \frac{12}{\sqrt{55}} = \frac{12}{7.42} = 1.62"
        result = convert_wikitext_to_html(f"<math>{formula}</math>")
        assert formula in result
        assert "\\(" in result
        assert "\\)" in result

    def test_math_not_mangled_by_bold_italic_pass(self) -> None:
        # LaTeX uses '' in \text{} constructs; bold/italic pass must not touch it
        result = convert_wikitext_to_html(r"<math>\text{if } x > 0</math>")
        assert r"\text{if } x > 0" in result

    def test_math_template_converted(self) -> None:
        # {{math|...}} is HTML-rendered: body is wikitext, wrapped in
        # <span class="texhtml"> (NOT KaTeX). Caret/asterisk are literal — the
        # author would use <sup> for superscript inside {{math}}.
        result = convert_wikitext_to_html("Let {{math|x + y = z}}.")
        assert '<span class="texhtml">x + y = z</span>' in result

    def test_mvar_template_converted(self) -> None:
        # {{mvar|x}} is always italic — single math variable.
        result = convert_wikitext_to_html("The variable {{mvar|x}} is real.")
        assert '<span class="texhtml"><i>x</i></span>' in result

    def test_math_with_apostrophe_italics(self) -> None:
        # The Calculus article's actual pattern: ''f''(''x'') inside {{math|...}}.
        # The wikitext italics must be converted to <em> by the downstream pass,
        # not left as literal apostrophes inside KaTeX delimiters.
        result = convert_wikitext_to_html("Then {{math|''f''(''x'')}}.")
        assert '<span class="texhtml"><em>f</em>(<em>x</em>)</span>' in result
        assert "\\(" not in result

    def test_math_with_equals_escape(self) -> None:
        # {{=}} expands to a literal `=` — needed because bare `=` in a template
        # param is interpreted as a named-param separator. Must be substituted,
        # not stripped (the prior behavior was a silent drop, leaving "y  mx").
        result = convert_wikitext_to_html("Then {{math|y {{=}} mx + b}}.")
        assert '<span class="texhtml">y = mx + b</span>' in result
        assert "{{=}}" not in result
        assert "y  mx" not in result  # double-space from old strip-and-drop behavior

    def test_math_with_pipe_escape(self) -> None:
        result = convert_wikitext_to_html("Set {{math|a {{!}} b}}.")
        assert '<span class="texhtml">a | b</span>' in result

    def test_math_with_sup_and_italics(self) -> None:
        # The Calculus article: {{math|''f''(''x'') {{=}} ''x''<sup>2</sup>}}.
        # HTML <sup> is passed through; wikitext italics convert to <em>;
        # {{=}} expands to =.
        result = convert_wikitext_to_html("If {{math|''f''(''x'') {{=}} ''x''<sup>2</sup>}}.")
        assert '<span class="texhtml">' in result
        assert "<em>f</em>(<em>x</em>) = <em>x</em><sup>2</sup>" in result

    def test_math_with_sub_tag(self) -> None:
        result = convert_wikitext_to_html("{{math|''x''<sub>0</sub>}}")
        assert '<span class="texhtml"><em>x</em><sub>0</sub></span>' in result

    def test_bigmath_renders_as_html_span(self) -> None:
        # {{bigmath|...}} is the larger HTML-rendered cousin — same wrapper.
        result = convert_wikitext_to_html("See {{bigmath|''y'' + 1}}.")
        assert '<span class="texhtml"><em>y</em> + 1</span>' in result

    def test_mvar_with_subscript_in_content(self) -> None:
        result = convert_wikitext_to_html("Index {{mvar|x<sub>0</sub>}}.")
        assert '<span class="texhtml"><i>x<sub>0</sub></i></span>' in result

    def test_tmath_template_converted(self) -> None:
        # {{tmath|...}} is Wikipedia's LaTeX-inline variant of {{math|...}};
        # like {{math}} it must wrap into a <math> tag (and then \(...\)) so
        # KaTeX renders it, not leak through as raw template syntax.
        result = convert_wikitext_to_html("Let {{tmath|x^2 + y^2 = z^2}}.")
        assert "{{tmath" not in result
        assert "\\(x^2 + y^2 = z^2\\)" in result

    def test_tmath_template_with_nested_braces(self) -> None:
        # The Calculus article writes {{tmath|\tfrac{\sin x}{x} }} with a trailing
        # space — required so mwparserfromhell doesn't fuse the LaTeX closing
        # brace with the template's closing `}}`. Both this canonical form and
        # bare `{{tmath|...}}` (no space) appear in real wikitext.
        result = convert_wikitext_to_html("Ratio {{tmath|\\tfrac{\\sin x}{x} }} of two functions.")
        assert "{{tmath" not in result
        assert "\\tfrac{\\sin x}{x}" in result

    def test_tmath_no_trailing_space_brace_balanced(self) -> None:
        # Bare `{{tmath|\tfrac{...}{...}}}` (no padding space before the closing
        # `}}`) must still parse: the string-level pre-pass balances inner braces
        # so the LaTeX body's final `}` isn't fused into the template close.
        result = convert_wikitext_to_html("Ratio {{tmath|\\tfrac{\\sin x}{x}}} approaches 1.")
        assert "{{tmath" not in result
        assert "\\(\\tfrac{\\sin x}{x}\\)" in result
        # And no stray `}` leaks out beside the rendered math.
        assert "\\(\\tfrac{\\sin x}{x}\\)}" not in result

    def test_math_no_trailing_space_brace_balanced(self) -> None:
        # Brace-balancing must still work for {{math|...}} even though the
        # template is now HTML-rendered: the body's literal `\frac{a}{b}` is
        # preserved (it won't render as a fraction — HTML-math is not LaTeX —
        # but at minimum the template wrapper closes correctly with no stray }.
        result = convert_wikitext_to_html("Let {{math|\\frac{a}{b}}}.")
        assert "{{math" not in result
        assert '<span class="texhtml">\\frac{a}{b}</span>' in result
        # No stray closing brace leaked outside the span.
        assert "</span>}" not in result

    def test_tmath_with_display_block_param_ignored(self) -> None:
        # `{{tmath|x|display=block}}` — the named `display=block` param is
        # extremely rare and mwparserfromhell's behavior took only the first
        # positional. The pre-pass preserves that: emit <math>x</math>, drop
        # the rest. (Real wikitext almost never uses this form.)
        result = convert_wikitext_to_html("Value {{tmath|x+1|display=block}} done.")
        assert "{{tmath" not in result
        assert "\\(x+1\\)" in result
        assert "display=block" not in result

    def test_math_with_top_level_pipe_in_brace(self) -> None:
        # A `|` *inside* nested braces is NOT a param separator: `\{a|b\}`
        # contains a bare pipe but it's inside `{...}`, so the full body
        # survives into the texhtml span rather than being split at the pipe.
        result = convert_wikitext_to_html("{{math|\\{a|b\\}}}")
        assert "{{math" not in result
        assert '<span class="texhtml">\\{a|b\\}</span>' in result

    def test_multiple_inline_formulas(self) -> None:
        result = convert_wikitext_to_html("When <math>\\mu = 0</math> and <math>\\sigma = 1</math>.")
        assert result.count("\\(") == 2
        assert result.count("\\)") == 2
        assert "\\mu = 0" in result
        assert "\\sigma = 1" in result

    def test_extract_and_restore_roundtrip(self) -> None:
        text = r'Inline <math>a + b</math> and block <math display="block">c = d</math>.'
        processed, math_blocks = _extract_math_tags(text)
        # Placeholders replace originals
        assert "<math>" not in processed
        assert "a + b" not in processed
        # Restore
        restored = _restore_math_tags(processed, math_blocks)
        assert "\\(a + b\\)" in restored
        assert "$$" in restored
        assert "c = d" in restored

    def test_empty_math_tag(self) -> None:
        # Empty math tags should not crash
        result = convert_wikitext_to_html("<math></math>")
        assert result is not None

    def test_bare_math_with_align_promoted_to_display(self) -> None:
        # Bare <math> (no display="block") containing \begin{align} must render as
        # display math — KaTeX rejects align in inline mode.
        wikitext = "<math>\n\\begin{align}\ny&=x^2 \\\\\n\\frac{dy}{dx}&=2x.\n\\end{align}\n</math>"
        result = convert_wikitext_to_html(wikitext)
        assert 'class="math-display"' in result
        assert "$$" in result
        assert "\\(" not in result
        assert "\\begin{align}" in result

    def test_bare_math_with_indented_align_promoted(self) -> None:
        # The Calculus article's Leibniz-notation form: leading ':' indent + bare
        # <math> + align. Must wrap in <dl><dd> and use display delimiters.
        wikitext = ":<math>\\begin{align}a&=b\\\\c&=d\\end{align}</math>"
        result = convert_wikitext_to_html(wikitext)
        assert "<dl>" in result
        assert "<dd>" in result
        assert 'class="math-display"' in result
        assert "$$" in result

    def test_bare_math_with_equation_env_promoted(self) -> None:
        result = convert_wikitext_to_html("<math>\\begin{equation}E=mc^2\\end{equation}</math>")
        assert 'class="math-display"' in result
        assert "\\(" not in result

    def test_bare_math_with_gather_env_promoted(self) -> None:
        result = convert_wikitext_to_html("<math>\\begin{gather}a\\\\b\\end{gather}</math>")
        assert 'class="math-display"' in result

    def test_bare_math_with_align_star_promoted(self) -> None:
        # The starred form (\begin{align*}) is also display-only.
        result = convert_wikitext_to_html("<math>\\begin{align*}x&=y\\end{align*}</math>")
        assert 'class="math-display"' in result

    def test_bare_math_with_aligned_stays_inline(self) -> None:
        # \begin{aligned} is a wrapping env — valid in inline math; do not promote.
        result = convert_wikitext_to_html("<math>\\begin{aligned}x&=y\\end{aligned}</math>")
        assert "\\(" in result
        assert 'class="math-display"' not in result

    def test_bare_math_with_cases_stays_inline(self) -> None:
        result = convert_wikitext_to_html("<math>f(x) = \\begin{cases}1 & x>0 \\\\ 0 & x\\le 0\\end{cases}</math>")
        assert "\\(" in result
        assert 'class="math-display"' not in result

    def test_bare_math_with_pmatrix_stays_inline(self) -> None:
        result = convert_wikitext_to_html("<math>\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}</math>")
        assert "\\(" in result
        assert 'class="math-display"' not in result

    def test_yes_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{yes}}")
        assert '<span class="indicator-yes">Yes</span>' in result

    def test_no_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{no}}")
        assert '<span class="indicator-no">No</span>' in result

    def test_partial_indicator_template(self) -> None:
        result = convert_wikitext_to_html("{{partial}}")
        assert '<span class="indicator-partial">Partial</span>' in result

    def test_indicator_in_table(self) -> None:
        wikitext = """{| class="wikitable"
! Feature !! Supported
|-
| Feature A || {{yes}}
|-
| Feature B || {{no}}
|-
| Feature C || {{partial}}
|}"""
        result = convert_wikitext_to_html(wikitext)
        assert '<span class="indicator-yes">Yes</span>' in result
        assert '<span class="indicator-no">No</span>' in result
        assert '<span class="indicator-partial">Partial</span>' in result
        assert "<table" in result

    def test_indicator_variants(self) -> None:
        # Test various template name variants
        assert "indicator-yes" in convert_wikitext_to_html("{{tick}}")
        assert "indicator-yes" in convert_wikitext_to_html("{{checked}}")
        assert "indicator-no" in convert_wikitext_to_html("{{cross}}")
        assert "indicator-unknown" in convert_wikitext_to_html("{{dunno}}")
        assert "indicator-na" in convert_wikitext_to_html("{{n/a}}")


class TestSectionLinkTemplates:
    def test_section_link_basic(self) -> None:
        """Test basic {{Section link|Page#Section}} conversion."""
        result = convert_wikitext_to_html("{{Section link|Ferrofluid#Heat transfer}}")
        assert 'data-wiki-title="Ferrofluid"' in result
        assert 'data-wiki-anchor="Heat_transfer"' in result
        assert ">Ferrofluid#Heat transfer</a>" in result

    def test_section_link_with_label(self) -> None:
        """Test {{Section link|Page#Section|Label}} with custom label."""
        result = convert_wikitext_to_html("{{Section link|Ferrofluid#Heat transfer|heat transfer}}")
        assert 'data-wiki-title="Ferrofluid"' in result
        assert 'data-wiki-anchor="Heat_transfer"' in result
        assert ">heat transfer</a>" in result

    def test_section_link_in_list(self) -> None:
        """Test section link within a list context."""
        wikitext = """== See also ==
* [[Ferrofluid]]
* {{Section link|Ferrofluid#Heat transfer}}
* [[Audio system]]"""
        result = convert_wikitext_to_html(wikitext)
        assert 'data-wiki-title="Ferrofluid"' in result
        assert 'data-wiki-title="Ferrofluid"' in result
        assert 'data-wiki-anchor="Heat_transfer"' in result
        assert 'data-wiki-title="Audio system"' in result


class TestReflistTemplate:
    def test_reflist_with_cite_web(self) -> None:
        wikitext = (
            "{{Reflist|2|refs=\n"
            "<ref name=Foo>{{cite web|title=Some Article|url=https://example.com|date=2020}}</ref>\n"
            "}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "Some Article" in result
        assert 'href="https://example.com"' in result
        assert "2020" in result

    def test_reflist_multiple_refs(self) -> None:
        wikitext = (
            "{{Reflist|refs=\n"
            "<ref name=A>{{cite web|title=First|url=https://first.com}}</ref>\n"
            "<ref name=B>{{cite web|title=Second|url=https://second.com}}</ref>\n"
            "}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert "First" in result
        assert "Second" in result
        assert 'href="https://first.com"' in result
        assert 'href="https://second.com"' in result

    def test_reflist_without_refs_param_removed(self) -> None:
        """Plain {{Reflist}} with no refs= is silently removed."""
        result = convert_wikitext_to_html("Some text.\n{{Reflist}}\nMore text.")
        assert "{{Reflist}}" not in result
        assert "Some text." in result

    def test_reflist_ref_ids(self) -> None:
        """Each <li> gets an id so anchor links can target it."""
        wikitext = "{{Reflist|refs=\n<ref name=MyRef>{{cite web|title=Target|url=https://x.com}}</ref>\n}}"
        result = convert_wikitext_to_html(wikitext)
        assert 'id="ref_MyRef"' in result


class TestInlineRefCollection:
    def test_unnamed_inline_ref_rendered(self) -> None:
        wikitext = "Text.<ref>{{cite book |last=Smith |title=Foo |date=2020}}</ref>\n{{Reflist}}"
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "Smith" in result
        assert "<em>Foo</em>" in result
        assert "2020" in result

    def test_named_inline_ref_rendered(self) -> None:
        wikitext = (
            'Text.<ref name="Ballou2008">{{cite book |last=Ballou |title=Handbook |date=2008}}</ref>\n{{Reflist}}'
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert 'id="ref_Ballou2008"' in result
        assert "Ballou" in result

    def test_named_ref_appears_multiple_times(self) -> None:
        """Named ref with content appearing twice in the body renders twice."""
        wikitext = (
            'First.<ref name="A">{{cite book |title=Alpha |date=2021}}</ref> '
            'Second.<ref name="A">{{cite book |title=Alpha |date=2021}}</ref>\n'
            "{{Reflist}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert result.count("Alpha") == 2

    def test_back_ref_not_collected(self) -> None:
        wikitext = 'First.<ref name="B">{{cite book |title=Beta |date=2022}}</ref> Second.<ref name="B"/>\n{{Reflist}}'
        result = convert_wikitext_to_html(wikitext)
        assert result.count("Beta") == 1

    def test_multiple_mixed_refs(self) -> None:
        wikitext = (
            "A.<ref>{{cite book |title=Unnamed1 |date=2001}}</ref> "
            'B.<ref name="Named">{{cite book |title=Named1 |date=2002}}</ref> '
            "C.<ref>{{cite book |title=Unnamed2 |date=2003}}</ref>\n"
            "{{Reflist}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert "Unnamed1" in result
        assert "Named1" in result
        assert "Unnamed2" in result
        assert result.count("<li") >= 3

    def test_plain_text_ref_fallback(self) -> None:
        wikitext = "Text.<ref>A plain-text footnote without a template.</ref>\n{{Reflist}}"
        result = convert_wikitext_to_html(wikitext)
        assert "plain-text footnote" in result
        assert '<ol class="references">' in result

    def test_bare_reflist_no_inline_refs_still_silent(self) -> None:
        result = convert_wikitext_to_html("Some text.\n{{Reflist}}\nMore text.")
        assert '<ol class="references">' not in result
        assert "{{Reflist}}" not in result

    def test_reflist_refs_param_unaffected(self) -> None:
        wikitext = (
            "{{Reflist|refs=\n<ref name=Foo>{{cite web|title=ExplicitRef|url=https://ex.com|date=2020}}</ref>\n}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "ExplicitRef" in result
        assert 'href="https://ex.com"' in result

    def test_unnamed_ref_id_is_numeric(self) -> None:
        wikitext = (
            "A.<ref>{{cite book |title=First |date=2001}}</ref> "
            "B.<ref>{{cite book |title=Second |date=2002}}</ref>\n"
            "{{Reflist}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert 'id="ref_1"' in result
        assert 'id="ref_2"' in result

    def test_no_reflist_inline_refs_stripped(self) -> None:
        wikitext = "Text.<ref>{{cite book |title=Gone |date=2020}}</ref> More text."
        result = convert_wikitext_to_html(wikitext)
        assert "<ref>" not in result
        assert "Gone" not in result


class TestReferencesTag:
    """Bare <references /> / <references>...</references> as a reflist alternative."""

    def test_self_closed_references_renders_inline_refs(self) -> None:
        wikitext = "Text.<ref>{{cite book |title=Foo |date=2020}}</ref>\n<references />"
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "Foo" in result

    def test_open_close_references_renders_body_refs(self) -> None:
        wikitext = (
            'Text<ref name="A" />.\n'
            "<references>\n"
            '<ref name="A">{{cite book |title=AlphaBook |date=2020}}</ref>\n'
            "</references>"
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "AlphaBook" in result
        assert 'id="ref_A"' in result

    def test_self_closed_with_no_inline_refs_renders_nothing(self) -> None:
        result = convert_wikitext_to_html("Some text.\n<references />\nMore text.")
        assert '<ol class="references">' not in result
        assert "<references" not in result

    def test_references_after_reflist_not_duplicated(self) -> None:
        wikitext = "A.<ref>{{cite book |title=Once |date=2020}}</ref>\n{{Reflist}}\n<references />"
        result = convert_wikitext_to_html(wikitext)
        assert result.count('<ol class="references">') == 1
        assert result.count("Once") == 1

    def test_self_closing_inline_ref_resolves_against_references_body(self) -> None:
        wikitext = (
            'First<ref name="X" />. Second<ref name="X" />.\n'
            "<references>\n"
            '<ref name="X">{{cite book |title=DefBody |date=2021}}</ref>\n'
            "</references>"
        )
        result = convert_wikitext_to_html(wikitext)
        assert '<ol class="references">' in result
        assert "DefBody" in result


class TestExternalLinks:
    """[url] and [url label] become clickable, opens-in-new-tab anchors."""

    def test_external_link_with_label(self) -> None:
        result = convert_wikitext_to_html("See [https://example.com Example Site].")
        assert 'href="https://example.com"' in result
        assert 'target="_blank"' in result
        assert 'rel="noopener noreferrer"' in result
        assert ">Example Site</a>" in result

    def test_external_link_without_label_numbered(self) -> None:
        result = convert_wikitext_to_html("Citation [https://first.example.com] and [https://second.example.com].")
        assert ">[1]</a>" in result
        assert ">[2]</a>" in result

    def test_external_link_label_keeps_inline_html(self) -> None:
        result = convert_wikitext_to_html("See [https://example.com ''italic'' text].")
        assert "<em>italic</em>" in result
        assert 'href="https://example.com"' in result

    def test_bare_url_autolinked(self) -> None:
        result = convert_wikitext_to_html("Visit https://example.com for more.")
        assert 'href="https://example.com"' in result
        assert ">https://example.com</a>" in result

    def test_bare_url_trailing_punctuation_kept_outside_link(self) -> None:
        result = convert_wikitext_to_html("Source: https://example.com.")
        assert 'href="https://example.com"' in result
        assert "</a>." in result

    def test_wikilink_not_treated_as_external(self) -> None:
        result = convert_wikitext_to_html("See [[Python]] not [http://x.com Py].")
        assert 'data-wiki-title="Python"' in result
        assert 'href="http://x.com"' in result

    def test_url_inside_attribute_not_double_linked(self) -> None:
        wikitext = "{{cite web|title=T|url=https://example.com|date=2020}}"
        result = convert_wikitext_to_html(f"text<ref>{wikitext}</ref>\n{{{{Reflist}}}}")
        assert result.count('href="https://example.com"') == 1


class TestMagicWords:
    """MediaWiki behaviour switches like __TOC__ should not leak into output."""

    def test_toc_stripped(self) -> None:
        result = convert_wikitext_to_html("Intro.\n__TOC__\nBody.")
        assert "__TOC__" not in result
        assert "Intro" in result and "Body" in result

    def test_multi_word_magic_word_stripped(self) -> None:
        result = convert_wikitext_to_html("__EXPECTED_UNCONNECTED_PAGE__\nBody.")
        assert "EXPECTED_UNCONNECTED_PAGE" not in result
        assert "Body" in result

    def test_lowercase_dunder_preserved(self) -> None:
        # Python-style __init__ isn't a magic word — only ALLCAPS form is.
        result = convert_wikitext_to_html("Method __init__ does setup.")
        assert "__init__" in result


class TestTransclusionTags:
    """`<noinclude>` keeps content, `<includeonly>` strips it, `<onlyinclude>` keeps it."""

    def test_noinclude_content_kept(self) -> None:
        result = convert_wikitext_to_html("Before <noinclude>visible content</noinclude> after.")
        assert "visible content" in result
        assert "<noinclude>" not in result

    def test_includeonly_content_stripped(self) -> None:
        result = convert_wikitext_to_html("Before <includeonly>hidden content</includeonly> after.")
        assert "hidden content" not in result
        assert "<includeonly>" not in result

    def test_onlyinclude_content_kept(self) -> None:
        result = convert_wikitext_to_html("Before <onlyinclude>shown content</onlyinclude> after.")
        assert "shown content" in result
        assert "<onlyinclude>" not in result


class TestCoordTemplate:
    """{{coord}} → formatted <span class="geo">."""

    def test_decimal_coords(self) -> None:
        result = convert_wikitext_to_html("Located at {{coord|40.4467|-79.9817}}.")
        assert '<span class="geo">' in result
        assert "40.4467°N" in result
        assert "79.9817°W" in result

    def test_dms_coords(self) -> None:
        result = convert_wikitext_to_html("At {{coord|40|26|46|N|79|58|56|W}}.")
        assert "40°26′46″N" in result
        assert "79°58′56″W" in result

    def test_dm_coords(self) -> None:
        result = convert_wikitext_to_html("At {{coord|40|26|N|79|58|W}}.")
        assert "40°26′N" in result
        assert "79°58′W" in result

    def test_coord_with_named_metadata(self) -> None:
        result = convert_wikitext_to_html("At {{coord|40|26|46|N|79|58|56|W|name=Pittsburgh|display=title}}.")
        assert "40°26′46″N" in result
        assert "Pittsburgh" not in result


class TestShortDescription:
    def test_short_description_dropped(self) -> None:
        result = convert_wikitext_to_html("{{short description|Capital of France}}\nParis is…")
        assert "Capital of France" not in result
        assert "Paris" in result


class TestPassthroughFirstArg:
    """Allowlist templates render only their first positional arg as text."""

    def test_quote_renders_text_drops_author(self) -> None:
        result = convert_wikitext_to_html("Famous: {{quote|To be or not to be|Hamlet}}.")
        assert "To be or not to be" in result
        assert "Hamlet" not in result

    def test_cquote_renders_text(self) -> None:
        result = convert_wikitext_to_html("{{cquote|Centered quote text}}")
        assert "Centered quote text" in result

    def test_as_of_keeps_label(self) -> None:
        result = convert_wikitext_to_html("Population, {{as of|2024}}, was 1M.")
        assert "as of 2024" in result

    def test_as_of_with_month_year(self) -> None:
        result = convert_wikitext_to_html("{{as of|2024|3}}")
        assert "as of March 2024" in result


class TestSfnTemplate:
    """{{sfn}} short footnotes render as numbered superscript markers."""

    def test_sfn_renders_as_numbered_sup(self) -> None:
        result = convert_wikitext_to_html("Claim{{sfn|Smith|2010|p=42}}.")
        assert '<sup class="sfn">[1]</sup>' in result

    def test_multiple_sfn_get_incrementing_indices(self) -> None:
        result = convert_wikitext_to_html("First{{sfn|Smith|2010}}. Second{{sfn|Jones|2011}}.")
        assert "[1]" in result
        assert "[2]" in result

    def test_sfnp_recognized(self) -> None:
        result = convert_wikitext_to_html("X{{sfnp|Smith|2010|p=1}}.")
        assert '<sup class="sfn">[1]</sup>' in result


class TestLangDashFamily:
    def test_lang_fr_renders_with_language_prefix(self) -> None:
        result = convert_wikitext_to_html("She said {{lang-fr|bonjour}}.")
        assert "French" in result
        assert "<em>bonjour</em>" in result

    def test_lang_de_renders(self) -> None:
        result = convert_wikitext_to_html("The {{lang-de|Bundestag}} meets.")
        assert "German" in result
        assert "<em>Bundestag</em>" in result


class TestPoemTag:
    """<poem> preserves line breaks and wikitext formatting."""

    def test_poem_lines_separated_by_br(self) -> None:
        wikitext = "<poem>\nFirst line\nSecond line\nThird line\n</poem>"
        result = convert_wikitext_to_html(wikitext)
        assert '<div class="poem">' in result
        assert "First line<br>Second line<br>Third line" in result

    def test_poem_preserves_bold_italic(self) -> None:
        wikitext = "<poem>\n''italic'' word\n'''bold''' word\n</poem>"
        result = convert_wikitext_to_html(wikitext)
        assert "<em>italic</em>" in result
        assert "<strong>bold</strong>" in result

    def test_poem_preserves_wikilink(self) -> None:
        wikitext = "<poem>\nVisit [[Python]] often\n</poem>"
        result = convert_wikitext_to_html(wikitext)
        assert 'data-wiki-title="Python"' in result

    def test_poem_asterisk_not_treated_as_list(self) -> None:
        wikitext = "<poem>\n* literal asterisk start\n</poem>"
        result = convert_wikitext_to_html(wikitext)
        assert "<ul>" not in result
        assert "* literal asterisk" in result


class TestUnsupportedBlockTags:
    def test_score_replaced_with_placeholder(self) -> None:
        wikitext = "Before <score>\\relative c' { c d e f }</score> after."
        result = convert_wikitext_to_html(wikitext)
        assert '<div class="unsupported-content">' in result
        assert "unsupported: score" in result
        assert "\\relative" not in result

    def test_timeline_replaced(self) -> None:
        wikitext = "<timeline>\nImageSize = width:200 height:50\n</timeline>"
        result = convert_wikitext_to_html(wikitext)
        assert "unsupported: timeline" in result
        assert "ImageSize" not in result

    def test_hiero_replaced(self) -> None:
        result = convert_wikitext_to_html("<hiero>N5-Z1-Z1</hiero>")
        assert "unsupported: hiero" in result


class TestChemistryTags:
    def test_chem_tag_becomes_inline_ce(self) -> None:
        result = convert_wikitext_to_html("Water is <chem>H2O</chem>.")
        assert "\\ce{H2O}" in result

    def test_ce_legacy_tag(self) -> None:
        result = convert_wikitext_to_html("Equation: <ce>2H2 + O2 -> 2H2O</ce>")
        assert "\\ce{2H2 + O2 -> 2H2O}" in result


class TestCitationAuthorVariants:
    def test_authors_plural_field_used(self) -> None:
        wikitext = "<ref>{{cite journal|authors=Smith, Jones, Doe|title=Foo|date=2020}}</ref>\n{{Reflist}}"
        result = convert_wikitext_to_html(wikitext)
        assert "Smith, Jones, Doe" in result

    def test_vauthors_field_used(self) -> None:
        wikitext = "<ref>{{cite journal|vauthors=Smith J, Doe AB|title=Foo|date=2020}}</ref>\n{{Reflist}}"
        result = convert_wikitext_to_html(wikitext)
        assert "Smith J, Doe AB" in result


class TestConvertConnectors:
    def test_by_connector(self) -> None:
        result = convert_wikitext_to_html("Dimensions: {{convert|2|by|4|m}}.")
        assert "2–4 m" in result

    def test_x_connector(self) -> None:
        result = convert_wikitext_to_html("{{convert|2|x|4|ft}}")
        assert "2–4 ft" in result

    def test_density_unit_renders(self) -> None:
        result = convert_wikitext_to_html("Density: {{convert|7.8|g/cm3}}.")
        assert "7.8 g/cm³" in result


class TestExternalLinksSectionKept:
    """The '== External links ==' section is rendered like any other section
    now that external-link conversion makes its contents useful."""

    def test_external_links_section_rendered(self) -> None:
        wikitext = "Intro text.\n\n== External links ==\n* [http://example.com Example]\n"
        result = convert_wikitext_to_html(wikitext)
        assert "External links" in result
        assert 'href="http://example.com"' in result
        assert ">Example</a>" in result
        assert "Intro text" in result

    def test_following_section_still_rendered(self) -> None:
        wikitext = "Intro.\n\n== External links ==\n* [http://example.com Example]\n\n== See also ==\n* [[Python]]\n"
        result = convert_wikitext_to_html(wikitext)
        assert "External links" in result
        assert "See also" in result
        assert 'href="http://example.com"' in result

    def test_case_insensitive_section_rendered(self) -> None:
        wikitext = "Intro.\n\n== external links ==\n* [http://example.com Example]\n"
        result = convert_wikitext_to_html(wikitext)
        assert "external links" in result
        assert 'href="http://example.com"' in result

    def test_unrelated_section_unaffected(self) -> None:
        wikitext = "== History ==\nSome history text.\n"
        result = convert_wikitext_to_html(wikitext)
        assert "History" in result
        assert "history text" in result


class TestTableTemplateHandlers:
    """Templates commonly used inside table cells that were previously stripped."""

    def test_rn_template_standalone(self) -> None:
        result = convert_wikitext_to_html("{{rn|VII}}")
        assert "font-variant:small-caps" in result
        assert "VII" in result

    def test_rn_template_in_table_cell(self) -> None:
        wikitext = """{| class="wikitable"
! Number !! Roman
|-
| 1 || {{rn|I}}
|-
| 4 || {{rn|IV}}
|}"""
        result = convert_wikitext_to_html(wikitext)
        assert "font-variant:small-caps" in result
        assert ">I<" in result
        assert ">IV<" in result

    def test_rn_individual_decimal_places_table(self) -> None:
        wikitext = """{| class="wikitable"
|+ Individual decimal places
|-
! !! Thousands !! Hundreds !! Tens !! Units
|-
| 1 || {{rn|M}} || {{rn|C}} || {{rn|X}} || {{rn|I}}
|-
| 2 || {{rn|MM}} || {{rn|CC}} || {{rn|XX}} || {{rn|II}}
|}"""
        result = convert_wikitext_to_html(wikitext)
        assert ">M<" in result
        assert ">C<" in result
        assert ">X<" in result
        assert ">I<" in result
        assert ">MM<" in result

    def test_nowrap_template(self) -> None:
        result = convert_wikitext_to_html("{{nowrap|hello world}}")
        assert "white-space:nowrap" in result
        assert "hello world" in result

    def test_ipa_template(self) -> None:
        result = convert_wikitext_to_html("{{ipa|/ˈɪŋɡlɪʃ/}}")
        assert "/ˈɪŋɡlɪʃ/" in result

    def test_ipac_en_template(self) -> None:
        result = convert_wikitext_to_html("{{ipac-en|ˈɪŋɡlɪʃ}}")
        assert "ˈɪŋɡlɪʃ" in result

    def test_nts_template(self) -> None:
        result = convert_wikitext_to_html("{{nts|42}}")
        assert "42" in result

    def test_sort_template_shows_display(self) -> None:
        result = convert_wikitext_to_html("{{sort|000123|123 km}}")
        assert "123 km" in result

    def test_sort_template_single_arg(self) -> None:
        result = convert_wikitext_to_html("{{sort|abc}}")
        assert "abc" in result

    def test_sortname_template(self) -> None:
        result = convert_wikitext_to_html("{{sortname|John|Smith}}")
        assert "John Smith" in result

    def test_tooltip_template(self) -> None:
        result = convert_wikitext_to_html("{{tooltip|NATO|North Atlantic Treaty Organization}}")
        assert "<abbr" in result
        assert "North Atlantic Treaty Organization" in result
        assert "NATO" in result

    def test_flag_template_shows_country(self) -> None:
        result = convert_wikitext_to_html("{{flag|France}}")
        assert "France" in result

    def test_flagicon_template_removed(self) -> None:
        result = convert_wikitext_to_html("Gold {{flagicon|USA}} {{sortname|Michael|Phelps}}")
        assert "flagicon" not in result
        assert "Michael Phelps" in result

    def test_dts_year_month_day(self) -> None:
        result = convert_wikitext_to_html("{{dts|2023|1|15}}")
        assert "January 15, 2023" in result

    def test_dts_year_month(self) -> None:
        result = convert_wikitext_to_html("{{dts|2023|3}}")
        assert "March 2023" in result

    def test_dts_year_only(self) -> None:
        result = convert_wikitext_to_html("{{dts|2023}}")
        assert "2023" in result

    def test_increasenegative_indicator(self) -> None:
        result = convert_wikitext_to_html("{{increasenegative}}")
        assert "▲" in result
        assert "indicator-increase-negative" in result

    def test_decreasepositive_indicator(self) -> None:
        result = convert_wikitext_to_html("{{decreasepositive}}")
        assert "▼" in result
        assert "indicator-decrease-positive" in result

    def test_frac_two_args(self) -> None:
        result = convert_wikitext_to_html("{{frac|1|1728}}")
        assert "1" in result
        assert "1728" in result
        assert "⁄" in result

    def test_frac_one_arg(self) -> None:
        result = convert_wikitext_to_html("{{frac|4}}")
        assert "4" in result
        assert "⁄" in result

    def test_frac_mixed_number(self) -> None:
        result = convert_wikitext_to_html("{{frac|1|1|2}}")
        assert "1" in result
        assert "2" in result
        assert "⁄" in result

    def test_frac_in_table_cell(self) -> None:
        wikitext = """{| class="wikitable"
! Fraction !! Name
|-
| {{frac|1|288}} || Scripulum
|-
| {{frac|1|72}} || Sextula
|}"""
        result = convert_wikitext_to_html(wikitext)
        assert "288" in result
        assert "72" in result
        assert "⁄" in result


class TestTaxonomyBoxes:
    def test_speciesbox_renders_as_infobox(self) -> None:
        wikitext = "{{speciesbox|name=Barley|image=foo.jpg|genus=Hordeum|species=vulgare|authority=L.}}"
        result = convert_wikitext_to_html(wikitext)
        assert '<table class="infobox">' in result
        assert "<caption>Barley</caption>" in result
        assert "Hordeum" in result

    def test_speciesbox_name_not_a_row(self) -> None:
        wikitext = "{{speciesbox|name=Barley|genus=Hordeum|species=vulgare}}"
        result = convert_wikitext_to_html(wikitext)
        assert "<th>Name</th>" not in result

    def test_speciesbox_image_skipped(self) -> None:
        wikitext = "{{speciesbox|name=Barley|image=foo.jpg|genus=Hordeum}}"
        result = convert_wikitext_to_html(wikitext)
        assert "foo.jpg" not in result

    def test_taxobox_renders_as_infobox(self) -> None:
        wikitext = "{{taxobox|name=Banana|regnum=Plantae|ordo=Zingiberales|genus=Musa}}"
        result = convert_wikitext_to_html(wikitext)
        assert '<table class="infobox">' in result
        assert "<caption>Banana</caption>" in result
        assert "Plantae" in result

    def test_taxobox_no_name_falls_back(self) -> None:
        wikitext = "{{speciesbox|genus=Hordeum|species=vulgare}}"
        result = convert_wikitext_to_html(wikitext)
        assert "<caption>Species</caption>" in result

    def test_speciesbox_synonyms_via_collapsible_plainlist(self) -> None:
        wikitext = (
            "{{speciesbox|name=Barley|genus=Hordeum|synonyms="
            "{{Collapsible list|{{Plainlist|style=x|"
            "*''Frumentum hordeum'' nom. illeg.\n"
            "*''Hordeum hexastichon''\n"
            "}}}}}}"
        )
        result = convert_wikitext_to_html(wikitext)
        assert "<th>Synonyms</th>" in result
        assert "Frumentum hordeum" in result
        assert "Hordeum hexastichon" in result
