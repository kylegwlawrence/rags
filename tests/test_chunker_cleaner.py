"""Unit tests for the cleaner + chunker.

Inline `Doc` fixtures only — no on-disk db reads. Existing `tests/test_smoke.py`
covers the API end-to-end against the dirty pre-refactor `_rag.db` files.
"""

from rag import Doc
from rag.chunker import chunk_doc, chunk_markdown
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html, strip_markdown


def test_cleaner_version_is_nonempty_string() -> None:
    assert isinstance(CLEANER_VERSION, str)
    assert CLEANER_VERSION


def test_strip_html_passthrough_when_no_tags_or_entities() -> None:
    text = "plain text with no markup"
    assert strip_html(text) is text


def test_strip_html_removes_tags() -> None:
    out = strip_html("a<br>b<p>c</p>")
    assert "<" not in out
    assert ">" not in out
    assert "a" in out and "b" in out and "c" in out


def test_strip_html_decodes_bare_entities() -> None:
    # Entities decode to their literal characters (correct for embedding):
    # the audit's complaint was that `&amp;` reached the embedder as five
    # tokens; after decoding it's one ampersand.
    assert strip_html("foo &amp; bar") == "foo & bar"
    assert "&lt;" not in strip_html("a &lt; b")
    assert "&amp;" not in strip_html("a &amp; b")


def test_strip_markdown_preserves_heading_text() -> None:
    out = strip_markdown("## Geography\nfoo bar")
    assert "Geography" in out
    assert "##" not in out


def test_strip_markdown_unwraps_bold_and_italic() -> None:
    assert strip_markdown("**foo** and _bar_") == "foo and bar"
    assert strip_markdown("__a__ and *b*") == "a and b"


def test_strip_markdown_collapses_links_to_visible_text() -> None:
    assert strip_markdown("see [the docs](https://x.y) for more") == "see the docs for more"


def test_normalize_whitespace_preserves_paragraph_breaks() -> None:
    assert normalize_whitespace("a\n\n\n\nb") == "a\n\nb"
    assert normalize_whitespace("a\n\nb") == "a\n\nb"


def test_normalize_whitespace_collapses_horizontal_runs() -> None:
    assert normalize_whitespace("a   b") == "a b"
    assert normalize_whitespace("a\n  b") == "a\nb"


def test_chunk_doc_returns_empty_on_empty_input() -> None:
    doc = Doc(doc_id="x", title="T", version="v", text="", section=None)
    assert chunk_doc(doc, chunk_size=200, max_chunk_size=240) == []


def test_chunk_doc_respects_hard_cap_on_no_break_input() -> None:
    no_break = "x" * 5000
    doc = Doc(doc_id="x", title="T", version="v", text=no_break, section=None)
    chunks = chunk_doc(doc, chunk_size=400, max_chunk_size=500)
    assert chunks, "expected at least one chunk"
    assert all(c["text_length"] <= 500 for c in chunks)


def test_chunk_doc_no_midword_starts_on_normal_english() -> None:
    sentences = "The quick brown fox jumps over the lazy dog. " * 60
    doc = Doc(doc_id="x", title="T", version="v", text=sentences, section=None)
    chunks = chunk_doc(doc, chunk_size=400, max_chunk_size=500)
    # Every chunk should start with a non-lowercase character (capital letter,
    # punctuation, or digit). Lowercase-starting chunks were the bug class
    # the refactor targeted.
    bad = [c for c in chunks if c["text"][:1].islower()]
    assert not bad, f"chunks starting mid-word: {[c['text'][:60] for c in bad]}"


def test_chunk_markdown_puts_section_in_metadata_not_body() -> None:
    md = "## Geography\nLocation: somewhere\nArea: big\n\n## Economy\nGDP: high"
    doc = Doc(doc_id="x", title="T", version="v", text=md, section="Default")
    chunks = chunk_markdown(doc, chunk_size=1000, max_chunk_size=1200)
    assert {c["section"] for c in chunks} == {"Geography", "Economy"}
    assert all("##" not in c["text"] for c in chunks)
    assert all("Geography" not in c["text"] for c in chunks if c["section"] == "Economy")


def test_chunk_markdown_handles_lead_text_without_heading() -> None:
    md = "Some intro paragraph before any heading.\n\n## Geography\nLocation"
    doc = Doc(doc_id="x", title="T", version="v", text=md, section="Lead")
    chunks = chunk_markdown(doc, chunk_size=1000, max_chunk_size=1200)
    sections = [c["section"] for c in chunks]
    assert "Lead" in sections or "Geography" in sections
    # Lead text either falls under doc.section ("Lead") or is consumed by the
    # first heading split — both are acceptable; the only requirement is no
    # information loss.
    joined = " ".join(c["text"] for c in chunks)
    assert "intro paragraph" in joined
    assert "Location" in joined


def test_chunk_markdown_chunk_index_is_global_document_order() -> None:
    # chunk_index runs 0..N-1 across the whole document (reading order), not
    # per-section. A per-section reset made the doc-chunks inspector interleave
    # sections when ordering by chunk_index.
    long_geo = "Location: somewhere. " * 100
    long_eco = "GDP: high. " * 100
    md = f"## Geography\n{long_geo}\n\n## Economy\n{long_eco}"
    doc = Doc(doc_id="x", title="T", version="v", text=md, section=None)
    chunks = chunk_markdown(doc, chunk_size=300, max_chunk_size=400)
    geo = [c for c in chunks if c["section"] == "Geography"]
    eco = [c for c in chunks if c["section"] == "Economy"]
    assert geo and eco
    # Indices are a contiguous 0..N-1 sequence in list order...
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    # ...so Geography occupies the front and Economy starts after it.
    assert geo[0]["chunk_index"] == 0
    assert eco[0]["chunk_index"] == len(geo)
