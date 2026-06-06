"""Per-source chunker profiles (chunk_size, max_chunk_size, overlap).

Scripts import their defaults from here; the API's live-embed routes import the same profile
so button-embedded docs chunk identically to batch-indexed ones. Changing a profile requires
--reset on the relevant indexer (version keys are content-derived, not config-derived).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkerProfile:
    """Chunker knobs for one source. Frozen so misuse-as-mutable is loud."""
    chunk_size: int       # soft target chars per chunk
    max_chunk_size: int   # hard cap; chunks above this re-split with " " separators
    overlap: int          # inter-chunk overlap chars (within-section for chunk_markdown)


# Soft default: most narrative prose (arxiv abstracts/HTML, openalex,
# federal_register, sec_edgar filings, python_docs, github READMEs).
DEFAULT = ChunkerProfile(chunk_size=1500, max_chunk_size=1800, overlap=150)

# Dense key:value or short-paragraph content (factbook, openfoodfacts).
# Smaller chunks keep one fact per vector and improve retrieval grain.
DENSE = ChunkerProfile(chunk_size=1000, max_chunk_size=1200, overlap=100)

# Long-form narrative (Project Gutenberg books). Larger chunks reduce the
# vector count without losing intra-paragraph context.
LONG_FORM = ChunkerProfile(chunk_size=2000, max_chunk_size=2400, overlap=300)

# simplewiki/enwiki: tuned smaller (~200 tokens/chunk) for accurate retrieval on the
# small Ollama models used here. The API's live-embed button imports these.
SIMPLEWIKI = ChunkerProfile(chunk_size=800, max_chunk_size=1000, overlap=100)

# enwiki: same settings as simplewiki — same Ollama models, same retrieval goals.
ENWIKI = SIMPLEWIKI
