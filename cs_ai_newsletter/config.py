"""Env-driven configuration for the newsletter package.

Every knob has a default so the package runs with no setup; override any value
with the matching environment variable for portability across machines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable runtime settings, read once from the environment."""

    arxiv_db: str          # read-only source DB (this repo's arxiv.db)
    newsletter_db: str     # this package's own read/write DB
    ollama_url: str        # base URL of the local Ollama server
    model: str             # generation model tag
    category: str          # arxiv primary_category to cover
    summary_num_ctx: int   # num_ctx for the per-paper map calls
    compose_num_ctx: int   # num_ctx for the single reduce call

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, falling back to defaults."""
        return cls(
            arxiv_db=os.environ.get(
                "NEWSLETTER_ARXIV_DB", "/datasets/arxiv/arxiv.db"),
            newsletter_db=os.environ.get(
                "NEWSLETTER_DB", "data/newsletter/newsletter.db"),
            ollama_url=os.environ.get(
                "NEWSLETTER_OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("NEWSLETTER_MODEL", "qwen3.5:9b"),
            category=os.environ.get("NEWSLETTER_CATEGORY", "cs.AI"),
            summary_num_ctx=int(os.environ.get("SUMMARY_NUM_CTX", "8192")),
            compose_num_ctx=int(os.environ.get("COMPOSE_NUM_CTX", "65536")),
        )
