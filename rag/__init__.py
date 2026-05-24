"""Shared RAG primitives plus any source-specific code reused by the API.

Mostly generic: chunking, embedding, hybrid retrieval, the rag-DB schema.
Each datasource has its own `data/<source>/<source>_rag.db` with a uniform
schema (chunks, chunks_fts, chunks_vec, docs_meta, _meta). The reader API
and the indexer scripts both import this package; most per-source "extractor"
code lives next to its indexer script under `scripts/`.

A few modules here are source-specific extractors that live in `rag/` only
because both a script and the API need to import them — keeping them under
`scripts/<source>/` would force the API to sys.path-mangle to reach them:

- `rag.wikitext`   — simplewiki / enwiki wikitext → markdown
- `rag.render`     — arxiv LaTeXML HTML → markdown
- `rag.sec_filing` — SEC EDGAR submission fetch + primary-document extraction

The embedding model is locked to `nomic-embed-text:v1.5` at 768 dimensions.
Changing the model means rebuilding every `<source>_rag.db` from scratch.
"""

import hashlib
from dataclasses import dataclass
from typing import NamedTuple


def content_hash(*parts: str | None) -> str:
    """SHA-256 hex prefix (32 chars / 128 bits) of NUL-joined parts.

    Used by every per-source extractor that needs a stable version key when
    the source schema lacks a per-row `updated_at` column. NUL separators
    prevent boundary-collision tricks (`"ab"+"c"` vs `"a"+"bc"`).
    """
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


@dataclass(frozen=True)
class Doc:
    """One unit of content from a source's raw DB, ready to chunk + embed.

    Attributes:
        doc_id: TEXT id stable across re-runs (arxiv id, openalex W-id, etc.).
        title: Short display string; used in the embedder's format_document prefix.
        version: Change-detection key. Re-runs skip docs whose version matches
            the previously-stored value in docs_meta.
        text: Full text to chunk. Concatenation of title/abstract/sections is
            the extractor's job.
        section: Optional default section name applied to every chunk produced
            from this doc. Per-chunk section overrides are extractor-specific.
    """

    doc_id: str
    title: str
    version: str
    text: str
    section: str | None = None


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk with its provenance and RRF score."""

    chunk_id: int
    doc_id: str
    title: str
    section: str | None
    chunk_index: int
    text: str
    text_length: int
    score: float


class RetrievalResult(NamedTuple):
    """Return value of `retriever.retrieve`.

    `used_dense` is False when Ollama was unreachable and only sparse (FTS5)
    results were merged. Callers should surface this to clients.
    """

    hits: list[Hit]
    used_dense: bool
