"""Extract one Doc per arxiv paper for the RAG indexer.

Papers with downloaded HTML (`papers.html_content`) get rendered into
section-tagged markdown via `rag.html_to_markdown.html_to_markdown` and consumed by
`rag.chunker.chunk_markdown` downstream, so each chunk carries its paper
section (Abstract / Introduction / Methods / Results / ...) in the
`chunks.section` column. Papers without HTML fall back to abstract-only
chunking; the paper title goes via `Doc.title` -> `format_document` at
embed time, so duplicating it in the chunk body would inflate the payload
with no extra signal.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.html_to_markdown import html_to_markdown


def iter_docs(arxiv_conn: sqlite3.Connection, limit: int | None = None) -> Iterator[Doc]:
    """Yield one Doc per row in `arxiv.papers`, optionally capped to `limit`.

    `Doc.text` is the rendered markdown body when `papers.html_content` is
    present; otherwise the cleaned abstract (or, only as a last resort when
    abstract is also empty, the title). `Doc.title` always carries the paper
    title, which `format_document` prepends at embed time. `version` is
    `papers.oai_datestamp` when present (otherwise a content hash so
    re-extracts still detect upstream edits) plus an 8-char hash of the html
    body when present (so a paper that gets HTML downloaded later re-embeds
    even when its OAI datestamp didn't move), plus `CLEANER_VERSION` so any
    cleaning-pipeline change invalidates every previously-stored chunk.

    The live-embed router (`api.routers.arxiv.embed_paper`) duplicates this
    construction inline using the same primitives — keep the two in sync if
    either changes.
    """
    if limit is not None:
        cursor = arxiv_conn.execute(
            "SELECT id, title, abstract, html_content, oai_datestamp, updated_date "
            "FROM papers ORDER BY id LIMIT ?",
            (limit,),
        )
    else:
        cursor = arxiv_conn.execute(
            "SELECT id, title, abstract, html_content, oai_datestamp, updated_date "
            "FROM papers ORDER BY id"
        )
    for row in cursor:
        title = normalize_whitespace(strip_html(row["title"] or ""))
        html_content = row["html_content"]
        text = html_to_markdown(html_content).strip() if html_content else ""
        if not text:
            # No HTML body, or render produced nothing — fall back to abstract.
            # Title is already in the embed prefix via format_document; only
            # use it as a body if there's literally nothing else to embed.
            abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
            text = abstract or title
        # html-body hash so newly-downloaded papers re-embed even when their
        # oai_datestamp didn't change.
        html_marker = content_hash(html_content)[:8] if html_content else "no-html"
        base_version = row["oai_datestamp"] or content_hash(
            title, row["abstract"] or "", row["updated_date"]
        )
        yield Doc(
            doc_id=row["id"],
            title=title or row["id"],
            version=f"{base_version}-{html_marker}-{CLEANER_VERSION}",
            text=text,
            section=None,
        )
