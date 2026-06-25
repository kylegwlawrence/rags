"""Catalog of the semantic-search (RAG) data sources.

Single source of truth for two consumers:

  * ``GET /sources`` — the API endpoint a client (or a small routing model)
    reads to decide which RAG server to query.
  * The "Search routing" section of ``DATASOURCES.md`` — generated from this
    list by ``scripts/gen_datasources.py``; a test guards against drift.

Each entry's ``chunks_endpoint`` is the hybrid-search path for that source.
Timeframes are static strings (routing hints), not live-measured.
"""

# One entry per RAG-enabled source. `id` is the catalog key used in
# DATASOURCES.md; `chunks_endpoint` is the real route path (a few differ from
# the id, e.g. github_readmes -> /github/chunks).
SOURCES: list[dict[str, str]] = [
    {
        "id": "arxiv",
        "name": "arXiv",
        "description": "Cutting-edge research papers in science, math, and "
        "computer science. Use for questions about new findings, theories, "
        "algorithms, or technical methods.",
        "timeframe": "1991–current",
        "chunks_endpoint": "/arxiv/chunks",
    },
    {
        "id": "openalex",
        "name": "OpenAlex",
        "description": "Finding academic papers and their citations across all "
        "fields. Use to discover studies, authors, or how influential research "
        "is.",
        "timeframe": "historical–current",
        "chunks_endpoint": "/openalex/chunks",
    },
    {
        "id": "openstax",
        "name": "OpenStax",
        "description": "College textbooks explaining math, science, business, "
        "and social science topics. Use for learning or explaining established "
        "academic concepts.",
        "timeframe": "current",
        "chunks_endpoint": "/openstax/chunks",
    },
    {
        "id": "gutenberg",
        "name": "Project Gutenberg",
        "description": "Classic public-domain books and literature. Use for "
        "questions about novels, authors, poems, or older famous texts.",
        "timeframe": "classic works",
        "chunks_endpoint": "/gutenberg/chunks",
    },
    {
        "id": "pydocs",
        "name": "Python Documentation",
        "description": "Official Python programming documentation. Use for "
        "Python syntax, standard library functions, and how to code in Python.",
        "timeframe": "current",
        "chunks_endpoint": "/pydocs/chunks",
    },
    {
        "id": "simplewiki",
        "name": "Simple English Wikipedia",
        "description": "General encyclopedia facts in plain, simple language. "
        "Use for quick, easy explanations of common topics, people, and places.",
        "timeframe": "2026 snapshot",
        "chunks_endpoint": "/simplewiki/chunks",
    },
    {
        "id": "enwiki",
        "name": "English Wikipedia",
        "description": "Full encyclopedia covering almost any general-knowledge "
        "topic in depth. Use for detailed background on people, places, "
        "history, and concepts.",
        "timeframe": "2026 snapshot",
        "chunks_endpoint": "/enwiki/chunks",
    },
    {
        "id": "wikinews",
        "name": "Wikinews",
        "description": "News articles about past world events. Use for what "
        "happened in specific news stories, politics, or events.",
        "timeframe": "2004–2026",
        "chunks_endpoint": "/wikinews/chunks",
    },
    {
        "id": "factbook",
        "name": "CIA World Factbook",
        "description": "Facts and statistics about countries: geography, "
        "population, government, and economy. Use for country profiles and "
        "comparisons.",
        "timeframe": "current",
        "chunks_endpoint": "/factbook/chunks",
    },
    {
        "id": "sec_edgar",
        "name": "SEC EDGAR",
        "description": "U.S. company financial filings (10-K, 10-Q, 8-K). Use "
        "for company finances, business reports, and corporate disclosures.",
        "timeframe": "1993–current",
        "chunks_endpoint": "/sec_edgar/chunks",
    },
    {
        "id": "federal_register",
        "name": "Federal Register",
        "description": "U.S. government agency rules and official notices. Use "
        "for new or proposed federal regulations and agency actions.",
        "timeframe": "1994–current",
        "chunks_endpoint": "/federal_register/chunks",
    },
    {
        "id": "ecfr",
        "name": "Electronic Code of Federal Regulations",
        "description": "The current U.S. federal regulations (rules currently "
        'in effect). Use for "what does the law/regulation say right now" '
        "questions.",
        "timeframe": "current",
        "chunks_endpoint": "/ecfr/chunks",
    },
    {
        "id": "eurlex",
        "name": "EUR-Lex",
        "description": "European Union laws and legislation. Use for EU "
        "regulations, directives, and legal text.",
        "timeframe": "1952–2019",
        "chunks_endpoint": "/eurlex/chunks",
    },
    {
        "id": "github_readmes",
        "name": "GitHub READMEs",
        "description": "Documentation for open-source software projects. Use to "
        "find software tools, libraries, and what they do.",
        "timeframe": "2026 snapshot",
        "chunks_endpoint": "/github/chunks",
    },
    {
        "id": "pdfs",
        "name": "Personal PDFs",
        "description": "A personal collection of uploaded PDF documents. Use "
        "only for the user's own added files.",
        "timeframe": "personal uploads",
        "chunks_endpoint": "/pdfs/chunks",
    },
]

# Markers wrapping the generated block in DATASOURCES.md. The generator script
# rewrites only the text between these; everything else in the file is hand-kept.
MARKDOWN_BEGIN = "<!-- BEGIN GENERATED SOURCES (scripts/gen_datasources.py) -->"
MARKDOWN_END = "<!-- END GENERATED SOURCES -->"


def render_markdown_section() -> str:
    """Return the DATASOURCES.md routing block (markers included) from SOURCES."""
    lines = [MARKDOWN_BEGIN, ""]
    for s in SOURCES:
        lines.append(
            f"- **{s['id']}** (`{s['chunks_endpoint']}`) — "
            f"{s['description']} *({s['timeframe']})*"
        )
    lines.append("")
    lines.append(MARKDOWN_END)
    return "\n".join(lines)
