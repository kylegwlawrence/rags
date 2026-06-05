"""Top-level wikitext → HTML pipeline.

The pipeline is a fixed sequence of stages whose order is load-bearing:

  0. string-level pre-pass: rewrite {{math|...}} / {{tmath|...}} / {{mvar|...}}
     to <math>...</math> before mwparserfromhell sees them, working around its
     greedy double-brace matcher (templates.replace_math_templates)
  1. wikicode-level template handlers (templates.py): infobox, code, lang,
     indicator, section-link, citation, ref collection, reflist
  2. wikicode-level stripping (strip.py): templates, refs, comments, categories
  3. flatten to string
  4. extract code/math blocks behind placeholders (protect.py)
  5. block-level converters: tables, lists, headings (tables.py + blocks.py)
  6. inline converters: bold/italic, links (inline.py)
  7. paragraph wrapping (blocks.py)
  8. restore code/math from placeholders
  9. whitespace cleanup
"""

import html as _html
import re

import mwparserfromhell

_MEDIA_LINK_PREFIXES = ("file:", "image:", "media:")

from rag.wiki_render import strip
from rag.wiki_render.blocks import convert_headings, convert_lists, wrap_paragraphs
from rag.wiki_render.inline import convert_bold_italic, convert_external_links, convert_links
from rag.wiki_render.protect import (
    extract_math_tags,
    extract_poem_tags,
    extract_syntaxhighlight,
    restore_code_blocks,
    restore_math_tags,
    restore_poem_tags,
)
from rag.wiki_render.strip import (
    convert_gallery,
    replace_unsupported_blocks,
    strip_nowiki,
)
from rag.wiki_render.tables import convert_tables
from rag.wiki_render.templates import (
    collect_inline_refs,
    convert_annotated_link_templates,
    convert_citation_templates,
    convert_code_templates,
    convert_convert_templates,
    convert_coord_templates,
    convert_date_sorting_templates,
    convert_flag_templates,
    convert_hatnote_templates,
    convert_indicator_templates,
    convert_infobox_templates,
    convert_lang_templates,
    convert_list_body_templates,
    convert_passthrough_first_arg_templates,
    convert_references_tag,
    convert_reflist_template,
    convert_section_link_templates,
    convert_sfn_templates,
    convert_short_description_templates,
    convert_simple_inline_templates,
    convert_wikidata_templates,
    replace_math_templates,
)


def clean_extra_markup(text: str) -> str:
    """Collapse runs of blank lines and trim trailing whitespace per line."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def convert_wikitext_to_html(wikitext: str) -> str:
    """Convert wikitext to HTML.

    Wikilinks are emitted as ``<a class="wikilink" data-wiki-title="...">``
    anchors that the front-end resolves to in-app navigation within the active
    source.
    """
    if not wikitext or not wikitext.strip():
        return ""

    try:
        # 0. Pre-parse: convert {{math|...}} family templates to <math>...</math>
        # at the string level so mwparserfromhell's greedy double-brace matcher
        # can't truncate LaTeX bodies that end with `}` adjacent to `}}`.
        wikitext = replace_math_templates(wikitext)

        wikicode = mwparserfromhell.parse(wikitext)

        # 1. Wikicode-level templates that produce output (must run before strip).
        convert_wikidata_templates(wikicode)
        convert_annotated_link_templates(wikicode)
        convert_hatnote_templates(wikicode)
        convert_infobox_templates(wikicode)
        convert_code_templates(wikicode)
        convert_lang_templates(wikicode)
        convert_indicator_templates(wikicode)
        convert_section_link_templates(wikicode)
        convert_citation_templates(wikicode)
        convert_simple_inline_templates(wikicode)
        convert_list_body_templates(wikicode)
        convert_convert_templates(wikicode)
        convert_coord_templates(wikicode)
        convert_short_description_templates(wikicode)
        convert_sfn_templates(wikicode)
        convert_passthrough_first_arg_templates(wikicode)
        convert_flag_templates(wikicode)
        convert_date_sorting_templates(wikicode)
        collected_refs = collect_inline_refs(wikicode)
        convert_reflist_template(wikicode, collected_refs)

        # 2. Remove File/Image/Media wikilinks at the wikicode level before
        # flattening — the structured tree handles nested brackets in captions
        # that the string-level strip_categories regex cannot match.
        for wl in list(wikicode.filter_wikilinks(recursive=False)):
            if str(wl.title).strip().lower().startswith(_MEDIA_LINK_PREFIXES):
                wikicode.remove(wl)

        # 3. Flatten to string.
        text = str(wikicode)

        # 4. Strip remaining noise via regex (faster than wikicode.remove() loops).
        text = strip.strip_comments(text)
        text = strip.strip_transclusion_tags(text)
        # Render <references /> / <references>...</references> BEFORE strip_refs
        # eats them — collected_refs would otherwise be silently discarded for
        # articles that use the raw tag instead of {{Reflist}}.
        text = convert_references_tag(text, collected_refs)
        text = strip.strip_refs(text)
        text = strip.strip_magic_words(text)
        text = strip.strip_templates(text)
        text = strip.strip_categories(text)

        # 4b. Handle MediaWiki-specific HTML tags that survive the strip pass.
        text = strip_nowiki(text)
        text = convert_gallery(text)
        text = replace_unsupported_blocks(text)

        # 5. Protect code/math/poem from later string-level passes.
        text, code_blocks = extract_syntaxhighlight(text)
        text, math_blocks = extract_math_tags(text)
        text, poem_blocks = extract_poem_tags(text)

        # 6. Block-level structure first.
        text = convert_tables(text)
        text = convert_lists(text)
        text = convert_headings(text)

        # 7. Inline formatting (tables handle their own inline pass).
        text = convert_bold_italic(text)
        text = convert_links(text)
        # External links run after wikilinks so [[…]] is already replaced and
        # [http…] won't be mistaken for the inside of a wikilink.
        text = convert_external_links(text)

        # 8. Wrap remaining bare lines in paragraphs.
        text = wrap_paragraphs(text)

        # 9. Restore protected blocks.
        text = restore_code_blocks(text, code_blocks)
        text = restore_math_tags(text, math_blocks)
        text = restore_poem_tags(text, poem_blocks)

        # 10. Tidy.

        text = clean_extra_markup(text)
        return text.strip()

    except Exception:
        # Last-ditch fallback: never let a render bug crash the request handler.
        # The article is shown as escaped plaintext so the user sees *something*.
        return f"<p>{_html.escape(wikitext)}</p>"
