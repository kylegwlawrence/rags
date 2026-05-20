"""Unit tests for `rag.wikitext.wikitext_to_markdown`.

The renderer is the one piece of new logic that the smoke tests can't
exercise without a live simplewiki.db. These cases pin the section-headed
output shape, the redirect-skip behaviour, and the File:/Image:/Category:
wikilink stripping that the chunker depends on.
"""

from rag.wikitext import is_redirect, wikitext_to_markdown


def test_redirect_returns_empty():
    assert wikitext_to_markdown("#REDIRECT [[Other]]") == ""
    assert wikitext_to_markdown("#redirect [[Other]]") == ""
    assert wikitext_to_markdown("  #REDIRECT  [[Other]]") == ""
    assert is_redirect("#REDIRECT [[Other]]")
    assert not is_redirect("Just a normal paragraph.")


def test_empty_returns_empty():
    assert wikitext_to_markdown("") == ""
    assert wikitext_to_markdown(None) == ""  # type: ignore[arg-type]


def test_section_headings_become_markdown():
    wt = (
        "Lead paragraph.\n\n"
        "== History ==\n"
        "Body of history.\n\n"
        "=== Origins ===\n"
        "Origins body.\n\n"
        "==== Details ====\n"
        "Details body.\n"
    )
    out = wikitext_to_markdown(wt)
    assert "## History" in out
    assert "### Origins" in out
    assert "#### Details" in out
    # Heading body text should follow each heading.
    assert "Body of history." in out
    assert "Origins body." in out


def test_file_image_category_wikilinks_dropped():
    wt = (
        "Intro with [[File:foo.jpg|thumb|caption text that should not survive]] "
        "and [[Image:bar.png|alt]] and [[Category:Animals]] but [[Real link|kept]].\n"
    )
    out = wikitext_to_markdown(wt)
    assert "caption text that should not survive" not in out
    assert "File:" not in out
    assert "Image:" not in out
    assert "Category:" not in out
    # The "Real link" display text should survive.
    assert "kept" in out


def test_bold_italic_strip_to_plain_text():
    wt = "This is '''bold''' and ''italic''."
    out = wikitext_to_markdown(wt)
    assert "bold" in out
    assert "italic" in out
    assert "'''" not in out
    assert "''" not in out


def test_template_content_stripped():
    """mwparserfromhell.strip_code drops {{...}} templates; we document that here."""
    wt = "Before {{cite|author=Doe}} after."
    out = wikitext_to_markdown(wt)
    # The template body is dropped wholesale (a known limitation of strip_code).
    assert "cite" not in out.lower()
    assert "Before" in out
    assert "after" in out


def test_lead_before_first_heading_preserved():
    wt = "Lead text.\n\n== Section ==\nBody."
    out = wikitext_to_markdown(wt)
    # Lead must come before the first ## heading.
    lead_pos = out.find("Lead text")
    heading_pos = out.find("## Section")
    assert lead_pos != -1
    assert heading_pos != -1
    assert lead_pos < heading_pos


def test_no_heading_returns_body_only():
    wt = "Just one paragraph of body text with no sections."
    out = wikitext_to_markdown(wt)
    assert out == "Just one paragraph of body text with no sections."
