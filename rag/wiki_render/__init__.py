"""Wikipedia wikitext → HTML rendering package.

The public entry point is ``convert_wikitext_to_html``. The underscore-prefixed
re-exports preserve the flat-module API used by ``tests/test_wiki_render.py``.

This is the *display* renderer (wikitext → HTML for the article Content view).
It is distinct from ``rag.wikitext`` (wikitext → markdown for the embedding/RAG
pipeline) — the two serve different consumers and are intentionally separate.
"""

from rag.wiki_render.blocks import (
    convert_headings as _convert_headings,
    convert_lists as _convert_lists,
)
from rag.wiki_render.inline import (
    convert_bold_italic as _convert_bold_italic,
    convert_links as _convert_links,
)
from rag.wiki_render.pipeline import (
    clean_extra_markup as _clean_extra_markup,
    convert_wikitext_to_html,
)
from rag.wiki_render.protect import (
    extract_math_tags as _extract_math_tags,
    restore_math_tags as _restore_math_tags,
)
from rag.wiki_render.tables import (
    convert_tables as _convert_tables,
    parse_cell as _parse_cell,
)

__all__ = [
    "convert_wikitext_to_html",
    "_convert_bold_italic",
    "_convert_headings",
    "_convert_links",
    "_convert_lists",
    "_convert_tables",
    "_parse_cell",
    "_clean_extra_markup",
    "_extract_math_tags",
    "_restore_math_tags",
]
