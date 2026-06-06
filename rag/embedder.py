"""Ollama embedding calls (nomic-embed-text:v1.5, 768d). URL via OLLAMA_URL env var.
Task prefixes (search_document / search_query) are required for quality retrieval.
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
    """Prepend the search_document: prefix + title header. Encodes provenance in the vector."""
    header = f"{title} - {section}" if section else title
    return f"{EMBED_DOC_PREFIX}{header}\n\n{text}"


def format_query(query: str) -> str:
    """Apply the `search_query:` prefix to a user query before embedding."""
    return f"{EMBED_QUERY_PREFIX}{query}"


def embed_text(text: str, base_url: str = OLLAMA_URL) -> list[float]:
    """Embed one text via Ollama. Raises httpx.HTTPError after retries (retriever catches this)."""
    def _call() -> list[float]:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{base_url}/api/embed",
                json={"model": EMBED_MODEL, "input": [text]},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"][0]

    return with_retry(_call, httpx.HTTPError)


def embed_texts_batch(texts: list[str], base_url: str = OLLAMA_URL) -> list[list[float]]:
    """Embed multiple texts in one Ollama call. Long timeout (600s) for large batches."""
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
