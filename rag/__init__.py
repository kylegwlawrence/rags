"""Shared RAG primitives (chunking, embedding, retrieval, schema) plus source-specific
extractors that both scripts and the API import. Each source has its own `<source>_rag.db`
with a uniform schema. Embedding model: nomic-embed-text:v1.5 at 768 dimensions.
"""

import hashlib
from dataclasses import dataclass
from typing import NamedTuple


def content_hash(*parts: str | None) -> str:
    """SHA-256 hex prefix (32 chars) of NUL-joined parts. NUL prevents boundary-collision."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


@dataclass(frozen=True)
class Doc:
    """One unit of content from a source, ready to chunk + embed."""

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
    """`used_dense` is False when Ollama was unreachable (sparse-only fallback)."""

    hits: list[Hit]
    used_dense: bool
