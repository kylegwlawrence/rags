"""Shared RAG primitives: chunking, embedding, hybrid retrieval.

Each datasource has its own `data/<source>/<source>_rag.db` with a uniform
schema (chunks, chunks_fts, chunks_vec, docs_meta, _meta). The reader API and
the indexer scripts both import this package; per-source "extractor" code
lives next to its indexer script under `scripts/`.

The embedding model is locked to `nomic-embed-text:v1.5` at 768 dimensions.
Changing the model means rebuilding every `<source>_rag.db` from scratch.
"""

from dataclasses import dataclass
from typing import NamedTuple


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
