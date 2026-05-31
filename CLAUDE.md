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
python scripts/gutenberg/gutenberg_download.py      # rsync from ibiblio; --language (default en), --dry-run
python scripts/simplewiki/simplewiki_download.py
python scripts/simplewiki/simplewiki_parse.py
python scripts/simplewiki/simplewiki_index_rag.py   # data/simplewiki/simplewiki_rag.db
python scripts/python_docs/python_docs_download.py
python scripts/python_docs/python_docs_index_fts.py
python scripts/python_docs/python_docs_index_rag.py # data/pydocs/python_docs_rag.db
python scripts/loc/loc_download.py
python scripts/loc/loc_newspapers_download.py
python scripts/loc/loc_books_marc.py
python scripts/sec_edgar/sec_edgar_download.py        # filing metadata → data/sec_edgar/sec_edgar.db
python scripts/sec_edgar/sec_edgar_fetch_bodies.py    # fetch 10-K bodies (standalone; no indexing)
python scripts/sec_edgar/sec_edgar_index_fts.py       # filings_fts FTS5 index
python scripts/sec_edgar/sec_edgar_index_rag.py       # data/sec_edgar/sec_edgar_rag.db
python scripts/worldbank/worldbank_download.py        # indicators + observations → data/worldbank/worldbank.db
python scripts/billstatus/billstatus_download.py      # GPO BILLSTATUS XML → data/billstatus/billstatus.db
python scripts/billstatus/billstatus_index_fts.py     # bills_fts FTS5 index
python scripts/ceps/ceps_download.py                  # CEPS EurLex dump (Harvard Dataverse) → data/eurlex/eurlex.db
python scripts/eurlex/eurlex_index_rag.py             # data/eurlex/eurlex_rag.db
python scripts/ecfr/ecfr_download.py                  # eCFR titles + section text → data/ecfr/ecfr.db
python scripts/ecfr/ecfr_index_fts.py                 # regulations_fts FTS5 index
python scripts/pdfs/pdfs_ingest.py                    # PDFs in data/pdfs/incoming/ → data/pdfs/pdfs.db
```

## Running the API

```bash
source .venv/bin/activate
pip install -r requirements.txt   # first time only
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Port 8002 is fixed (8000/8001 occupied). Tailscale ACLs gate access; no app-level auth. `GET /health` returns per-DB status (503 if any DB broken).

**Reload:** after any indexer/downloader run, restart uvicorn — connections are cached at module load. Exceptions (live write paths, no restart needed): `POST /simplewiki/articles/{page_id}/embed` writes via a fresh RW connection (WAL mode makes the committed rows visible to the cached reader immediately); `POST /sec_edgar/filings/{accession_number}/download` does an in-place single-row UPDATE on `sec_edgar.db` via `db.connect_rw` — the cached read-only connection sees the committed row on its next query even though that DB isn't WAL (same file, no inode swap).

## API routes

All list endpoints: `limit` (default 50, max 200) + `offset` → `{items, total, limit, offset}`. Chunk endpoints: `q` (required), `top_k`, `candidate_k` → `{items, used_dense, top_k, candidate_k}` (RRF, not paginated). Missing FTS table → 503 with script name; bad FTS syntax → 400; Ollama down → sparse-only (`used_dense=false`).

- `/arxiv/papers`, `/{id:path}`, `/{id:path}/content`, `/arxiv/chunks`
- `/openalex/works`, `/{short_id}`, `/openalex/chunks`
- `/factbook/countries`, `/{id}`, `/factbook/chunks`
- `/gutenberg/texts`, `/{id}`, `/{id}/content`, `/gutenberg/chunks`
- `/simplewiki/articles`, `/{page_id}`, `/{page_id}/content`, `POST /{page_id}/embed`, `/simplewiki/chunks`
- `/enwiki/articles`, `/{page_id}`, `/{page_id}/content` — thin proxy to `scripts/enwiki/enwiki_remote_server.py` running on `raspberrypi6`. Read-only; no `/chunks` and no embed in v1.
- `/pydocs/docs`, `/{doc_path:path}`, `/{doc_path:path}/content`, `/pydocs/chunks`
- `/sec_edgar/filings` (`?downloaded=` true/false), `/{accession_number}`, `/{accession_number}/content`, `POST /{accession_number}/download`, `/sec_edgar/chunks`
- `/worldbank/indicators` (`?q=`, `?topic=`), `/indicators/{id}`, `/indicators/{id}/values` (`?country=`, `?year=`), `/worldbank/countries`, `/countries/{id}/data` (`?topic=`, `?year=`)
- `/billstatus/bills` (`?q=`, `?congress=`, `?bill_type=`, `?sponsor=`, `?policy_area=`, `?subject=`, `?sort=`), `/{bill_id}`, `/{bill_id}/content` — bill_id format is `{congress}-{TYPE}-{number}`, e.g. `118-HR-1234`. No RAG/chunks.
- `/ecfr/regulations` (`?q=`, `?title=`, `?part=`, `?embedded=`, `?sort=`), `/{reg_id}`, `/{reg_id}/content`, `POST /{reg_id}/embed`, `/ecfr/chunks` — one row per CFR section; `reg_id` is the integer row id. `?q=` is FTS5 over heading + content; `sort=relevance` requires `q`, else document (reading) order. RAG is on-demand only: there is no batch indexer (the full corpus is ~509k chunks ≈ 8 days on local Ollama), so sections are embedded one at a time via the embed button into `ecfr_rag.db`. `?embedded=` filters by chunk presence.
- `/pdfs/documents` (`?title=`, `?author=`), `/{doc_id}`, `/{doc_id}/content` — one row per ingested PDF; `doc_id` is the source filename stem. `/content` streams the **original PDF file** from `data/pdfs/incoming/` as `application/pdf` (inline disposition) so the frontend renders it in an in-browser viewer. No FTS, RAG, or chunks for this source.

The `/sec_edgar/filings` list (and detail) now surfaces metadata-only filings whose body hasn't been downloaded — `?downloaded=` narrows to fetched/unfetched. `POST /simplewiki/.../embed` and `POST /sec_edgar/.../download` are the only write paths in the API.

## Script notes

**arxiv**
- `arxiv_ingest.py` — OAI-PMH harvester. Rate: 3 s/req. Set `ARXIV_EMAIL`. Flags: `--from`, `--until`, `--db`, `--from-cache`, `--reset`. Restart after.
- `arxiv_download.py` — HTML body fetcher. Flags: `--db`, `--limit`, `--force`. Restart after.
- `arxiv_normalize_authors.py` — Backfill only for arxiv.db files predating Phase 3; idempotent.
- `arxiv_index_fts.py` — Rebuilds `papers_fts` (porter, external-content). Required for `?q=`.
- `arxiv_index_rag.py` — `arxiv_rag.db`. Chunks full HTML body (section-tagged markdown) when available; falls back to abstract-only for papers without downloaded HTML. Flags: `--limit`, `--reset`, `--batch`, `--chunk-size` (1500), `--max-chunk-size` (1800), `--overlap` (150). Restart after.

**factbook**
- `factbook_download.py` — Clones `github.com/factbook/factbook.json` → `factbook.db`.
- `factbook_index_rag.py` — `factbook_rag.db`. Flags: `--chunk-size` (1000), `--max-chunk-size` (1200), `--overlap` (100). Restart after.

**openalex**
- `openalex_download.py` — OpenAlex `/works` API. Set `OPENALEX_EMAIL` for polite-pool rate limit.
- `openalex_normalize_authors.py` — Builds `authors` / `work_authors`. Required for `?author=`. Re-runnable.
- `openalex_index_fts.py` — Rebuilds `works_fts` (~20 s, ~150 MB). Required for `?q=`.
- `openalex_index_rag.py` — `openalex_rag.db` (top-5k by citation count). Same flags as arxiv rag. Restart after.

**gutenberg**
- `gutenberg_download.py` — Fetches PG catalog CSV, filters by language, rsyncs matching files from ibiblio mirror. Flags: `--language` (default `en`; comma-separated codes or `all`), `--dry-run`.
- `gutenberg_index.py` — Walks `.txt` files, joins PG catalog CSV → `gutenberg.db`.
- `gutenberg_index_rag.py` — `gutenberg_rag.db`. Flags: `--language` (en), `--limit` (100), `--chunk-size` (2000), `--max-chunk-size` (2400), `--overlap` (300). Restart after.

**simplewiki**
- `simplewiki_download.py` — Downloads + SHA-1 verifies dump to `data/simplewiki/dumps/`.
- `simplewiki_parse.py` — Streams bz2 XML → `simplewiki.db`. Flags: `--all-namespaces`. Restart after.
- `simplewiki_index_rag.py` — `simplewiki_rag.db`. Default `--limit 100`; full 394k-article corpus ≈ 700 h. Flags: `--chunk-size` (800), `--max-chunk-size` (1000), `--overlap` (100). **Keep chunk settings in sync with `api/routers/simplewiki.py` `_CHUNK_SIZE`/`_MAX_CHUNK_SIZE`/`_OVERLAP`.** Restart after.

**enwiki** (remote, no local DB)
- The 76 GB `enwiki.db` is too big to keep on this machine, so it lives on `raspberrypi6:~/datasets/enwiki/enwiki.db`. A tiny FastAPI service serves it over Tailscale; the local API just proxies.
- `scripts/enwiki/enwiki_remote_server.py` — pi-side service. Routes: `GET /health`, `GET /articles` (with `?q=` title-FTS, `?title=` substring, `?namespace=`), `GET /articles/{page_id}`, `GET /articles/{page_id}/content`. Opens the DB read-only via `mode=ro`. No auth. Env: `ENWIKI_DB_PATH` overrides the default DB path.
- `api/routers/enwiki.py` — local proxy. Reads `ENWIKI_REMOTE_URL` (set in `.env`, e.g. `http://raspberrypi6:8765`). Returns 503 when unset or the pi is unreachable. The `/health` probe skips this source when the env var is unset so a developer without Tailscale doesn't see a red probe.
- Deploy update: `scp scripts/enwiki/enwiki_remote_server.py raspberrypi6:~/datasets/enwiki_remote_server.py`. The pi runs uvicorn in a tmux session named `enwiki`: `tmux new-session -d -s enwiki 'cd ~/datasets && exec .venv/bin/uvicorn enwiki_remote_server:app --host 0.0.0.0 --port 8765 2>&1 | tee /tmp/enwiki.log'`. Restart by killing the tmux session and re-running the same command.
- FTS5 `articles_fts` already exists on the pi DB but indexes **title only** (trigram tokeniser → 3+ char terms). Body FTS / RAG are deferred.

**python_docs**
- `python_docs_download.py` — Python docs text archive. Pass a pinned `--python-version` (e.g. `3.13`); the generic `3` redirect doesn't work for `.tar.bz2`.
- `python_docs_index_fts.py` — Rebuilds `docs_fts`. Required for `?q=`.
- `python_docs_index_rag.py` — `python_docs_rag.db`. Full run ≈ 513 pages; runtime not yet measured. Restart after.

**loc**
- `loc_download.py` — LOC search API. Flags: `--format`, `--language`. Resumes via `ingest_state`.
- `loc_newspapers_download.py` — Chronicling America metadata. Flags: `--date-from`, `--date-to`.
- `loc_books_marc.py` — MARC bulk files from `data/loc/raw/`. Requires `pymarc`. Not resumable.

**sec_edgar**
- `sec_edgar_download.py` — Quarterly full-index harvester (1993–present). Stores filing **metadata + URLs only**, no body text. Flags: `--db`, `--start-year`, `--end-year`, `--email` (`SEC_EMAIL` env), `--reset`. Resumes via `ingest_state`.
- `sec_edgar_fetch_bodies.py` — **Standalone** body fetcher: downloads filing `.txt` from `filing_url`, extracts the primary document, and stores **two** forms of it — the HTML-stripped clean text in `body` (used by FTS + RAG) and the render-ready HTML in `body_html` (served to the Content view); `status` tracks fetched/missing/error. Does **not** build any index. Defaults to 10-K, newest first, `--limit 200`. Flags: `--db`, `--accession` (fetch one filing by accession number, ignoring form-type/limit/status — always refetches), `--form-type`, `--limit`, `--email`, `--delay`, `--reset-status`. The fetch + extraction logic lives in `rag/sec_filing.py`; the API's `POST /sec_edgar/.../download` route reuses it in-process to fetch a single filing on demand (the "Download full filing" button). `body_html` only feeds rendering — adding/refilling it never changes `body`, so existing FTS / RAG indexes stay valid. Rows fetched before `body_html` existed render as `<pre>`-wrapped text until re-fetched (`--reset-status`).
- `sec_edgar_index_fts.py` — Rebuilds `filings_fts` (company_name + body, fetched rows only). Required for `?q=`.
- `sec_edgar_index_rag.py` — `sec_edgar_rag.db` over fetched bodies (`chunk_doc`, flat prose). Same flags as other RAG indexers. Restart after.

**worldbank**
- `worldbank_download.py` — Fetches all 21 topic-tagged indicator groups from the World Bank Indicators API v2. Stores topics, countries/aggregates, indicator metadata, and non-null observations. No API key required. Flags: `--db`, `--start-year` (default 2021), `--reset`. Resumable: completed indicators tracked in `completed_indicators` table. Runtime: ~1–2 h for full topic-tagged set (~5–7k indicators). Restart API after.

**billstatus**
- `billstatus_download.py` — Downloads GPO BILLSTATUS XML bulk zips per Congress/bill-type, extracts metadata + the latest CRS summary into `bills` (one row per bill, PK `{congress}-{TYPE}-{number}`). Covers 108th–present. Flags: `--db`, `--congress-from` (default: resume from `ingest_state`), `--congress-to` (default 119). Resumable via `ingest_state`.
- `billstatus_index_fts.py` — Rebuilds `bills_fts` over `title + summary + subjects` (porter, external-content). Required for `?q=`. Restart after. No RAG indexer (summaries are short).

**eurlex** (ingested by the `ceps` downloader — see note below)
- `eurlex_index_rag.py` — `eurlex_rag.db` over the `laws` bodies (`act_raw_text`, flat prose via `chunk_doc` — extracted PDF text carries no reliable `##` headings). Reads `data/eurlex/eurlex.db`; re-runnable, content-hash skip. Flags: `--limit` (full 142k corpus is many hours on local Ollama). Restart after. Per-row Doc construction lives in `rag/eurlex.py` (`build_doc`), shared with the API's live-embed route.
- `eurlex_rag_extract.py` — Indexer entry point: queries `laws` (non-empty `act_raw_text`, newest-first) and yields one Doc per row via `rag.eurlex.build_doc`. Not run directly; imported by `eurlex_index_rag.py`.

**ceps** (EUR-Lex ingest — lives in `scripts/ceps/`, writes into `data/eurlex/`)
- `ceps_download.py` — The **only** downloader for the EUR-Lex source. Pulls the CEPS EurLex dataset (142k EU laws, 1952–2019; a frozen snapshot, not incremental) from Harvard Dataverse (DOI `10.7910/DVN/0EGYWY`) into `data/eurlex/raw/`, then bulk-loads every CSV/tab into a dynamically-typed `laws` table (header columns sanitized to TEXT). The CSV ships the full law text in `act_raw_text`, so there is no separate body-fetch step. Flags: `--db` (default `data/eurlex/eurlex.db`), `--download-dir` (default `data/eurlex/raw`), `--reset` (drops + reimports `laws`). Idempotent: skips already-downloaded files and refuses to reimport a non-empty `laws` table without `--reset`. The `raw/` CSVs are only needed for a `--reset` reimport — safe to delete once `laws` is populated. There is no updater for laws past 2019.

**ecfr**
- `ecfr_download.py` — Fetches the current Electronic Code of Federal Regulations from the `ecfr.gov` versioner API. Walks all 50 CFR titles (Title 35 is reserved/empty), then stores one row per section in `regulations` (`title_num`, `title_name`, `chapter`, `part`, `section`, `heading`, `content`; `UNIQUE(title_num, section)`). Single current snapshot — no amendment history. Set `MAILTO`. Resumes via `ingest_state.completed_titles`.
- `ecfr_index_fts.py` — Rebuilds `regulations_fts` (porter, external-content) over `heading + content`, keyed on the `id` INTEGER PK. Required for `?q=`. ~20 s. Restart after.
- **No batch RAG indexer.** The full corpus is ~509k chunks (~8 days on local Ollama), so semantic search is on-demand: `POST /ecfr/regulations/{id}/embed` chunks one section (flat prose via `chunk_doc`, DEFAULT profile) into `data/ecfr/ecfr_rag.db`, the same live-embed pattern as enwiki. `ecfr_rag.db` is created empty (schema only) so the read-only opener and `/health` stay green before the first embed.

**pdfs**
- `pdfs_ingest.py` — Drop-folder ingester. Scans `data/pdfs/incoming/` for `*.pdf`, extracts per-page text + document metadata via **pdfplumber** into `pdfs.db` (`documents` + `pages` tables; `doc_id` = filename stem). Originals stay in the drop folder — the API's `/pdfs/documents/{doc_id}/content` route streams them in place. Idempotent: skips already-ingested `doc_id`s unless `--force`. Flags: `--db`, `--incoming`, `--force`. **No FTS/RAG indexer** — the frontend renders the original PDF in an `<iframe>` (`contentType: 'pdf'` in `frontend/sources.js`); there is no embed/chunks layer.

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
- `api/db.py` — read-only module-level SQLite connections; `connect_rag_rw` for live embed writes; `connect_rw` for the SEC live body-download write.
- `api/models.py` — `Page[T]` for list endpoints; `ChunksResponse` for RAG.
- `api/routers/` — one thin router per source; SQL inline.
- `api/_chunks.py` — shared chunks factory (400 empty `q`, 503 missing rag.db, sparse fallback when Ollama down).
- `api/_fts.py` — `translate_fts_errors`: missing table → 503, bad FTS5 syntax → 400.
- `rag/` — `chunker.py` (`chunk_doc` / `chunk_markdown`), `cleaner.py` (`CLEANER_VERSION`), `embedder.py` (nomic-embed-text:v1.5 768d, `OLLAMA_URL`), `render.py` (arxiv HTML→md), `wikitext.py`, `sec_filing.py` (SEC submission fetch + primary-document extraction into clean text *and* render HTML via `extract_primary`, shared by the fetch-bodies script and the API download route), `retriever.py` (RRF), `retry.py`, `schema.py`, `indexer.py`.
- `tests/` — pytest smoke suite; run with `pytest`.

Indexes are created by downloader/indexer scripts (API is read-only). Add `CREATE INDEX IF NOT EXISTS` to the relevant script when adding new filters.

## Conventions

- Each new source: script in `scripts/`, data in `data/<source>/`.
- SQLite: `INSERT OR REPLACE` / `INSERT OR IGNORE` for idempotent re-runs.

## Working rules

- Always ask clarifying questions before starting a coding task.
- Always pause and confirm before committing to git.
- Never run any indexer with `--reset` (or any other DB-wiping flag) without first
  describing what it will destroy and getting an explicit "yes" — rebuilds take
  hours on local Ollama and the data is gitignored. This applies to all the
  `scripts/*_index_*.py` scripts, the worldbank downloader's `--reset`, and any
  similar destructive flag elsewhere.
- Speak simply in plain terms — avoid unnecessary software jargon.
- Python: PEP 8, docstrings, code comments, type hints.
- Prefer stdlib; exceptions in `rag/`: `langchain-text-splitters` (chunker), `beautifulsoup4` (HTML stripping), `mwparserfromhell` (wikitext parsing).
- Small, modular pieces with clear responsibilities. DRY.
- Security: secrets handling, input validation, safe file/network use.
