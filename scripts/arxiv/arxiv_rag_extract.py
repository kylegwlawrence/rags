"""Extract one Doc per arxiv paper for the RAG indexer.

Papers with downloaded HTML (`papers.html_content`) get rendered into
section-tagged markdown via `rag.html_to_markdown.html_to_markdown` and consumed by
`rag.chunker.chunk_markdown` downstream, so each chunk carries its paper
section (Abstract / Introduction / Methods / Results / ...) in the
`chunks.section` column. Papers with only a PDF fallback (`papers.pdf_text`)
use that plain text; papers with neither fall back to abstract-only chunking.
The paper title goes via `Doc.title` -> `format_document` at embed time, so
duplicating it in the chunk body would inflate the payload with no extra signal.
"""

import sqlite3
from collections.abc import Iterator

from rag import Doc, content_hash
from rag.cleaner import CLEANER_VERSION, normalize_whitespace, strip_html
from rag.html_to_markdown import html_to_markdown


def iter_docs(arxiv_conn: sqlite3.Connection, limit: int | None = None) -> Iterator[Doc]:
    """Yield one Doc per row in `arxiv.papers`, optionally capped to `limit`.

    `Doc.text` is the rendered markdown body when `papers.html_content` is
    present, else the PDF-fallback text (`papers.pdf_text`), else the cleaned
    abstract (or, only as a last resort when abstract is also empty, the
    title). `Doc.title` always carries the paper title, which `format_document`
    prepends at embed time. `version` is `papers.oai_datestamp` when present
    (otherwise a content hash so re-extracts still detect upstream edits) plus
    an 8-char hash of whichever body exists (so a paper that gets an HTML or
    PDF body downloaded later re-embeds even when its OAI datestamp didn't
    move), plus `CLEANER_VERSION` so any cleaning-pipeline change invalidates
    every previously-stored chunk.

    The live-embed router (`api.routers.arxiv._build_doc`) duplicates this
    construction inline using the same primitives — keep the two in sync if
    either changes.
    """
    sql = (
        "SELECT id, title, abstract, html_content, pdf_text, oai_datestamp, "
        "updated_date FROM papers ORDER BY id"
    )
    if limit is not None:
        cursor = arxiv_conn.execute(sql + " LIMIT ?", (limit,))
    else:
        cursor = arxiv_conn.execute(sql)
    for row in cursor:
        title = normalize_whitespace(strip_html(row["title"] or ""))
        html_content = row["html_content"]
        pdf_text = row["pdf_text"]
        if html_content:
            text = html_to_markdown(html_content).strip()
        elif pdf_text:
            text = pdf_text.strip()
        else:
            text = ""
        if not text:
            # No body or empty render — fall back to abstract. Title is already
            # in the embed prefix, so only use it as a last resort.
            abstract = normalize_whitespace(strip_html(row["abstract"] or ""))
            text = abstract or title
        # body hash so newly-downloaded papers re-embed even when their
        # oai_datestamp didn't change.
        body = html_content or pdf_text
        body_marker = content_hash(body)[:8] if body else "no-body"
        base_version = row["oai_datestamp"] or content_hash(
            title, row["abstract"] or "", row["updated_date"]
        )
        yield Doc(
            doc_id=row["id"],
            title=title or row["id"],
            version=f"{base_version}-{body_marker}-{CLEANER_VERSION}",
            text=text,
            section=None,
        )
