"""Daily cs.AI arXiv newsletter — a self-contained package.

Summarizes each day's newly announced cs.AI arXiv papers for a lay reader
(map step), then consolidates the summaries into one themed newsletter
(reduce step). Stores issues in its own ``newsletter.db`` and serves them
over a small FastAPI router.

This package is intentionally decoupled: it imports nothing from this repo's
``api/`` or ``rag/`` packages. It brings its own SQLite access, its own Ollama
client, and its own response models so it can be lifted into a standalone repo
later (see ``README.md``).
"""

__all__ = ["config", "llm", "source", "store", "summarize", "compose", "pipeline"]
