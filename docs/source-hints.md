# Source hints

Tool `description` fields for each data source. Paste into a function-calling
or MCP tool definition. Convention: corpus identity → routing signal → scope
limit, all in one tight paragraph.

---

## `search_arxiv`

Search full-text chunks from arXiv preprint papers across physics, mathematics,
computer science, statistics, quantitative biology, and economics. Use when the
query concerns a scientific method, finding, or author in STEM fields — to check
what research exists on a topic, retrieve an abstract, or explore a technical
concept. Papers with downloaded HTML supply full body text (Abstract,
Introduction, Methods, Results, Discussion); others fall back to title and
abstract only.

## `search_openalex`

Search title and abstract chunks from the 5,000 most-cited academic works
across all disciplines, drawn from the OpenAlex open catalog. Use for
foundational or highly-cited research on any topic, cross-disciplinary academic
questions, or when citation count is a meaningful signal. Biased toward older,
established work — recent, niche, or lightly-cited research is unlikely to
appear.

## `search_factbook`

Search structured CIA World Factbook data for any of 260+ countries, organized
by section (Geography, Economy, Government, People and Society, Military,
Communications, Transportation). Use for country-level factual questions about
population, GDP, political system, climate, borders, exports, languages,
religion, or infrastructure. Coverage is strictly country-level — no city,
province, or sub-national breakdowns.

## `search_gutenberg`

Search paragraph-chunked full text of approximately 100 English public-domain
books from Project Gutenberg. Use when looking for passages, quotations,
characters, plot details, or themes in classic literature, philosophy, or
historical writing (works generally pre-1928). Not suitable for modern
copyrighted books or non-English texts in this local corpus.

## `search_simplewiki`

Search Simple English Wikipedia articles covering a broad range of encyclopedic
topics in plain, accessible language. Use for general factual background,
definitions, biographies, geography, history, or introductory science concepts.
Template content such as infoboxes, citations, and navigation boxes is stripped
from chunks. Does not cover events after the dump cutoff (early 2024).

## `search_pydocs`

Search official Python 3 documentation including the standard library reference,
language reference, tutorials, how-to guides, and what's-new changelogs. Use for
questions about Python's built-in modules (os, pathlib, asyncio, itertools,
etc.), built-in functions, language syntax, or Python-specific behavior. Does
not cover third-party packages such as NumPy or Django, nor other programming
languages.

## `search_wikihow`

Search step-by-step how-to guides from wikiHow on practical everyday tasks,
organized by guide, section, and individual step. Use for actionable instructions
on DIY projects, cooking, health and wellness, personal relationships, social
skills, or any "how do I…" question where sequential guidance is expected. Not
suitable for factual reference, academic research, or technical documentation.

## `search_enwiki`

Full English Wikipedia articles across all encyclopedic topics. Use when Simple
Wikipedia lacks sufficient depth. Results matched by title only (trigram, 3+
chars); no body FTS or RAG chunks in v1. Does not cover events after April 2026.
