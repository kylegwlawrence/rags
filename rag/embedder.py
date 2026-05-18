"""Ollama embedding calls for the RAG pipeline.

Locked to `nomic-embed-text:v1.5` at 768 dimensions (the tag matters; the bare
`nomic-embed-text` may resolve differently). The model's task-prefix contract
is enforced via `format_document` / `format_query` — embedding without them
produces noticeably worse retrieval.

Configurable via the `OLLAMA_URL` env var (default `http://localhost:11434`).
"""

import os
import struct
import time

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text:v1.5"
EMBEDDING_DIM = 768

EMBED_DOC_PREFIX = "search_document: "
EMBED_QUERY_PREFIX = "search_query: "

_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 2.0


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

    Retries up to 3x with exponential backoff on HTTP errors. Raises
    `httpx.HTTPError` if all attempts fail; the retriever catches this for the
    sparse-only fallback path.
    """
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            time.sleep(_BACKOFF_BASE**attempt)
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
        except httpx.HTTPError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise


def embed_texts_batch(texts: list[str], base_url: str = OLLAMA_URL) -> list[list[float]]:
    """Embed multiple texts in one HTTP round-trip via Ollama `/api/embed`.

    Used by the indexer's hot path — N texts go in one POST instead of N. Same
    retry policy as `embed_text` but a longer (120 s) timeout because the batch
    can include hundreds of chunks.
    """
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            time.sleep(_BACKOFF_BASE**attempt)
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{base_url}/api/embed",
                    json={"model": EMBED_MODEL, "input": texts},
                )
                resp.raise_for_status()
                return resp.json()["embeddings"]
        except httpx.HTTPError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise


def pack_embedding(embedding: list[float]) -> bytes:
    """Serialize a float32 vector to raw bytes for sqlite-vec storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def unpack_embedding(data: bytes) -> list[float]:
    """Deserialize raw bytes from sqlite-vec back to a float list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))
