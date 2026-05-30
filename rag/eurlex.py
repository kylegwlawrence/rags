"""EUR-Lex Doc-builder: render one `laws` row into a Doc.

Shared by the batch indexer (`scripts/eurlex/eurlex_rag_extract.py`) and the
API's live-embed route (`api.routers.eurlex.embed_law`). Lives in `rag/`
rather than `scripts/eurlex/` because both a script and the API need to
import it — same reasoning as `rag.federal_register`, `rag.sec_filing`, and
`rag.wikitext` (see the rag/__init__.py docstring).

The `laws` body (`act_raw_text`) is flat prose extracted from EUR-Lex PDFs —
no reliable `##` heading structure — so the indexer pairs this with
`rag.chunker.chunk_doc` rather than `chunk_markdown`.

`doc_id` is the CELEX number (e.g. `32019D0276`). Version key is
`content_hash(act_raw_text)` plus `CLEANER_VERSION`; the source has no
per-row content hash, so a hash of the body is the only edit-detection
signal — bumping `CLEANER_VERSION` invalidates all previously-indexed docs.
"""

import sqlite3

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace


def build_doc(row: sqlite3.Row) -> Doc | None:
    """Render one `laws` row into a Doc, or None when the row has no body.

    Expected columns on `row`: `CELEX, Act_name, act_raw_text`.
    """
    body = row["act_raw_text"] or ""
    if not body.strip():
        return None
    celex = row["CELEX"]
    title = normalize_whitespace(row["Act_name"] or "") or celex
    return Doc(
        doc_id=celex,
        title=title,
        version=f"{content_hash(body)}-{CLEANER_VERSION}",
        text=body,
        section=None,
    )
