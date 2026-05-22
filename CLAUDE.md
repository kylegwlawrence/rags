# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Personal collection of one-shot downloader scripts that fetch public datasets into local SQLite databases under `data/<source>/`, plus a read-only FastAPI app (`api/`) exposed over the Tailscale network. Each script is independent — no shared library, build system, or test suite. `data/` is gitignored.

## Running scripts

Activate the venv first; run all scripts from the repo root:

```bash
source .venv/bin/activate
python scripts/arxiv/arxiv_ingest.py                # OAI-PMH metadata → data/arxiv/arxiv.db
python scripts/arxiv/arxiv_download.py              # HTML bodies for papers
python scripts/arxiv/arxiv_normalize_authors.py     # one-shot backfill for legacy DBs only
python scripts/arxiv/arxiv_index_fts.py             # papers_fts FTS5 index
python scripts/arxiv/arxiv_index_rag.py             # data/arxiv/arxiv_rag.db
python scripts/factbook/factbook_download.py
python scripts/factbook/factbook_index_rag.py       # data/factbook/factbook_rag.db
python scripts/openalex/openalex_download.py
python scripts/openalex/openalex_normalize_authors.py  # required for ?author= filter
python scripts/openalex/openalex_index_fts.py       # works_fts FTS5 index
python scripts/openalex/openalex_index_rag.py       # data/openalex/openalex_rag.db (top-5k)
python scripts/gutenberg/gutenberg_index.py         # data/gutenberg/gutenberg.db
python scripts/gutenberg/gutenberg_index_rag.py     # data/gutenberg/gutenberg_rag.db
bash   scripts/gutenberg/gutenberg_download.sh
python scripts/simplewiki/simplewiki_download.py
python scripts/simplewiki/simplewiki_parse.py
python scripts/simplewiki/simplewiki_index_rag.py   # data/simplewiki/simplewiki_rag.db
python scripts/python_docs/python_docs_download.py
python scripts/python_docs/python_docs_index_fts.py
python scripts/python_docs/python_docs_index_rag.py # data/pydocs/python_docs_rag.db
python scripts/wikihow/wikihow_loader.py
python scripts/wikihow/wikihow_index_fts.py
python scripts/wikihow/wikihow_index_rag.py
python scripts/loc/loc_download.py
python scripts/loc/loc_newspapers_download.py
python scripts/loc/loc_books_marc.py
python scripts/sec_edgar/sec_edgar_download.py        # filing metadata → data/sec_edgar/sec_edgar.db
python scripts/sec_edgar/sec_edgar_fetch_bodies.py    # fetch 10-K bodies (standalone; no indexing)
python scripts/sec_edgar/sec_edgar_index_fts.py       # filings_fts FTS5 index
python scripts/sec_edgar/sec_edgar_index_rag.py       # data/sec_edgar/sec_edgar_rag.db
```

## Running the API

```bash
source .venv/bin/activate
pip install -r requirements.txt   # first time only
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Port 8002 is fixed (8000/8001 occupied). Tailscale ACLs gate access; no app-level auth. `GET /health` returns per-DB status (503 if any DB broken).

**Reload:** after any indexer/downloader run, restart uvicorn — connections are cached at module load. Exception: `POST /simplewiki/articles/{page_id}/embed` writes via a fresh RW connection; WAL mode makes the committed rows visible to the cached reader immediately.

## API routes

All list endpoints: `limit` (default 50, max 200) + `offset` → `{items, total, limit, offset}`. Chunk endpoints: `q` (required), `top_k`, `candidate_k` → `{items, used_dense, top_k, candidate_k}` (RRF, not paginated). Missing FTS table → 503 with script name; bad FTS syntax → 400; Ollama down → sparse-only (`used_dense=false`).

- `/arxiv/papers`, `/{id:path}`, `/{id:path}/content`, `/arxiv/chunks`
- `/openalex/works`, `/{short_id}`, `/openalex/chunks`
- `/factbook/countries`, `/{id}`, `/factbook/chunks`
- `/gutenberg/texts`, `/{id}`, `/{id}/content`, `/gutenberg/chunks`
- `/simplewiki/articles`, `/{page_id}`, `/{page_id}/content`, `POST /{page_id}/embed`, `/simplewiki/chunks`
- `/pydocs/docs`, `/{doc_path:path}`, `/{doc_path:path}/content`, `/pydocs/chunks`
- `/wikihow/articles`, `/{id}`, `/{id}/content`, `/wikihow/chunks`
- `/sec_edgar/filings`, `/{accession_number}`, `/{accession_number}/content`, `/sec_edgar/chunks`

`/wikihow/articles` rows are per-step (not whole guides); `/wikihow/chunks` reassembles whole guides. `POST /simplewiki/.../embed` is the only write path in the API.

## Script notes

**arxiv**
- `arxiv_ingest.py` — OAI-PMH harvester. Rate: 3 s/req. Set `ARXIV_EMAIL`. Flags: `--from`, `--until`, `--db`, `--from-cache`, `--reset`. Restart after.
- `arxiv_download.py` — HTML body fetcher. Flags: `--db`, `--limit`, `--force`. Restart after.
- `arxiv_normalize_authors.py` — Backfill only for arxiv.db files predating Phase 3; idempotent.
- `arxiv_index_fts.py` — Rebuilds `papers_fts` (porter, external-content). Required for `?q=`.
- `arxiv_index_rag.py` — `arxiv_rag.db`. Falls back to title+abstract when HTML unavailable. Flags: `--limit`, `--reset`, `--batch`, `--chunk-size` (1500), `--max-chunk-size` (1800), `--overlap` (150). Restart after.

**factbook**
- `factbook_download.py` — Clones `github.com/factbook/factbook.json` → `factbook.db`.
- `factbook_index_rag.py` — `factbook_rag.db`. Flags: `--chunk-size` (1000), `--max-chunk-size` (1200), `--overlap` (100). Restart after.

**openalex**
- `openalex_download.py` — OpenAlex `/works` API. Set `OPENALEX_EMAIL` for polite-pool rate limit.
- `openalex_normalize_authors.py` — Builds `authors` / `work_authors`. Required for `?author=`. Re-runnable.
- `openalex_index_fts.py` — Rebuilds `works_fts` (~20 s, ~150 MB). Required for `?q=`.
- `openalex_index_rag.py` — `openalex_rag.db` (top-5k by citation count). Same flags as arxiv rag. Restart after.

**gutenberg**
- `gutenberg_download.sh` — rsync via SSH to `pop-os`; requires that alias.
- `gutenberg_index.py` — Walks `.txt` files, joins PG catalog CSV → `gutenberg.db`.
- `gutenberg_index_rag.py` — `gutenberg_rag.db`. Flags: `--language` (en), `--limit` (100), `--chunk-size` (2000), `--max-chunk-size` (2400), `--overlap` (300). Restart after.

**simplewiki**
- `simplewiki_download.py` — Downloads + SHA-1 verifies dump to `data/simplewiki/dumps/`.
- `simplewiki_parse.py` — Streams bz2 XML → `simplewiki.db`. Flags: `--all-namespaces`. Restart after.
- `simplewiki_index_rag.py` — `simplewiki_rag.db`. Default `--limit 100`; full 394k-article corpus ≈ 700 h. Flags: `--chunk-size` (800), `--max-chunk-size` (1000), `--overlap` (100). **Keep chunk settings in sync with `api/routers/simplewiki.py` `_CHUNK_SIZE`/`_MAX_CHUNK_SIZE`/`_OVERLAP`.** Restart after.

**python_docs**
- `python_docs_download.py` — Python docs text archive. Pass a pinned `--python-version` (e.g. `3.13`); the generic `3` redirect doesn't work for `.tar.bz2`.
- `python_docs_index_fts.py` — Rebuilds `docs_fts`. Required for `?q=`.
- `python_docs_index_rag.py` — `python_docs_rag.db`. Full run ≈ 513 pages; runtime not yet measured. Restart after.

**wikihow**
- `wikihow_loader.py` — Loads `wikihowSep.csv` → `wikihow.db` (one row per step). Required before `/wikihow` routes work.
- `wikihow_index_fts.py` — Rebuilds `articles_fts`. Required for `?q=`.
- `wikihow_index_rag.py` — `wikihow_rag.db`. Default `--limit 100` caps guides (not step rows). Restart after.

**loc**
- `loc_download.py` — LOC search API. Flags: `--format`, `--language`. Resumes via `ingest_state`.
- `loc_newspapers_download.py` — Chronicling America metadata. Flags: `--date-from`, `--date-to`.
- `loc_books_marc.py` — MARC bulk files from `data/loc/raw/`. Requires `pymarc`. Not resumable.

**sec_edgar**
- `sec_edgar_download.py` — Quarterly full-index harvester (1993–present). Stores filing **metadata + URLs only**, no body text. Flags: `--db`, `--start-year`, `--end-year`, `--email` (`SEC_EMAIL` env), `--reset`. Resumes via `ingest_state`.
- `sec_edgar_fetch_bodies.py` — **Standalone** body fetcher: downloads filing `.txt` from `filing_url`, extracts the primary document, strips HTML, stores it in a new `body` column (`status` tracks fetched/missing/error). Does **not** build any index. Defaults to 10-K, newest first, `--limit 200`. Flags: `--db`, `--form-type`, `--limit`, `--email`, `--delay`, `--reset-status`.
- `sec_edgar_index_fts.py` — Rebuilds `filings_fts` (company_name + body, fetched rows only). Required for `?q=`.
- `sec_edgar_index_rag.py` — `sec_edgar_rag.db` over fetched bodies (`chunk_doc`, flat prose). Same flags as other RAG indexers. Restart after.

### Re-indexing notes

- **Chunker setting changes** (`--overlap`, `--chunk-size`, `--max-chunk-size`): version key is content-based, so changed settings don't trigger re-index. Pass `--reset` to rebuild from scratch.
- **`CLEANER_VERSION` bump:** forces re-embed of all docs on next run. Scripts are idempotent and resumable.

**Measured runtimes** (local Ollama, nomic-embed-text:v1.5, ~1.4 s/chunk):

| Source | Chunks | Estimate |
|--------|--------|----------|
| arxiv (1.2k papers) | ~1.6k | ~25-40 min |
| openalex (limit 5k) | ~8.5k | ~2.5-3 h |
| factbook (261 countries) | ~10k | ~3-4 h |
| gutenberg (limit 100) | ~14k | ~4-5 h |
| simplewiki limit 100 | ~hundreds | ~10 min |
| simplewiki full 394k | ~2M | ~700 h |

## API layout

- `api/main.py` — mounts routers, `/health`.
- `api/db.py` — read-only module-level SQLite connections; `connect_rag_rw` for live embed writes.
- `api/models.py` — `Page[T]` for list endpoints; `ChunksResponse` for RAG.
- `api/routers/` — one thin router per source; SQL inline.
- `api/_chunks.py` — shared chunks factory (400 empty `q`, 503 missing rag.db, sparse fallback when Ollama down).
- `api/_fts.py` — `translate_fts_errors`: missing table → 503, bad FTS5 syntax → 400.
- `rag/` — `chunker.py` (`chunk_doc` / `chunk_markdown`), `cleaner.py` (`CLEANER_VERSION`), `embedder.py` (nomic-embed-text:v1.5 768d, `OLLAMA_URL`), `render.py` (arxiv HTML→md), `wikitext.py`, `retriever.py` (RRF), `retry.py`, `schema.py`, `indexer.py`.
- `tests/` — pytest smoke suite; run with `pytest`.

Indexes are created by downloader/indexer scripts (API is read-only). Add `CREATE INDEX IF NOT EXISTS` to the relevant script when adding new filters.

## Conventions

- Each new source: script in `scripts/`, data in `data/<source>/`.
- SQLite: `INSERT OR REPLACE` / `INSERT OR IGNORE` for idempotent re-runs.

## Working rules

- Always ask clarifying questions before starting a coding task.
- Always pause and confirm before committing to git.
- Speak simply in plain terms — avoid unnecessary software jargon.
- Python: PEP 8, docstrings, code comments, type hints.
- Prefer stdlib; exceptions in `rag/`: `langchain-text-splitters` (chunker), `beautifulsoup4` (HTML stripping), `mwparserfromhell` (wikitext parsing).
- Small, modular pieces with clear responsibilities. DRY.
- Security: secrets handling, input validation, safe file/network use.
