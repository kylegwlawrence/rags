"""Ollama embedding calls for the RAG pipeline.

Locked to `nomic-embed-text:v1.5` at 768 dimensions (the tag matters; the bare
`nomic-embed-text` may resolve differently). The model's task-prefix contract
is enforced via `format_document` / `format_query` — embedding without them
produces noticeably worse retrieval.

Configurable via the `OLLAMA_URL` env var (default `http://localhost:11434`).
"""

import os
import struct

import httpx

from rag.retry import with_retry

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text:v1.5"
EMBEDDING_DIM = 768

EMBED_DOC_PREFIX = "search_document: "
EMBED_QUERY_PREFIX = "search_query: "


def format_document(title: str, section: str | None, text: str) -> str:
    """Build the indexing-time string for a chunk.

    Prepends nomic's `search_document:` task prefix, then a short header with
    the doc's title (and section heading when present) so the chunk vector
    encodes self-contained provenance instead of a fragment that depends on
    neighbouring chunks for context.
    """
    header = f"{title} - {section}" if section else title
    return f"{EMBED_DOC_PREFIX}{header}\n\n{text}"


def format_query(query: str) -> str:
    """Apply the `search_query:` prefix to a user query before embedding."""
    return f"{EMBED_QUERY_PREFIX}{query}"


def embed_text(text: str, base_url: str = OLLAMA_URL) -> list[float]:
    """Embed a single text via Ollama `/api/embeddings`.

    Retries up to `rag.retry.MAX_ATTEMPTS` times with exponential backoff on
    HTTP errors. Raises `httpx.HTTPError` if all attempts fail; the retriever
    catches this for the sparse-only fallback path.
    """
    def _call() -> list[float]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base_url}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]

    return with_retry(_call, httpx.HTTPError)


def embed_texts_batch(texts: list[str], base_url: str = OLLAMA_URL) -> list[list[float]]:
    """Embed multiple texts in one HTTP round-trip via Ollama `/api/embed`.

    Used by the indexer's hot path — N texts go in one POST instead of N. Same
    retry policy as `embed_text` but a longer (600 s) timeout because the batch
    can include hundreds of chunks and Ollama can stall under memory pressure
    (model swap-outs, parallel requests) for well past two minutes.
    """
    def _call() -> list[list[float]]:
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                f"{base_url}/api/embed",
                json={"model": EMBED_MODEL, "input": texts},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]

    return with_retry(_call, httpx.HTTPError)


def pack_embedding(embedding: list[float]) -> bytes:
    """Serialize a float32 vector to raw bytes for sqlite-vec storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)
