# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Personal collection of one-shot downloader scripts that fetch public datasets into local SQLite databases or plain files under `data/<source>/`, plus a small read-only FastAPI app (`api/`) that exposes them over the Tailscale network. Each downloader script is independent — there is no shared library, build system, or test suite. The `data/` directory is gitignored; only the scripts and the API are tracked.

## Running scripts

A Python venv lives at `.venv/`. Activate before running Python scripts:

```bash
source .venv/bin/activate
python scripts/arxiv_index_fts.py             # builds the papers_fts FTS5 index over data/arxiv/arxiv.db
python scripts/factbook_download.py
python scripts/openalex_download.py
python scripts/openalex_normalize_authors.py  # backfills authors + work_authors tables
python scripts/openalex_index_fts.py          # builds the works_fts FTS5 index
python scripts/gutenberg_index.py             # builds data/gutenberg/gutenberg.db from mirror + PG catalog
bash   scripts/kaggle_download.sh
bash   scripts/gutenberg_download.sh
```

`data/arxiv/arxiv.db` is not downloaded by a script in this repo — it's copied
from the `local_wikipedia` repo on this machine (the source of truth for arxiv
ingest). After each refresh-copy, re-run `scripts/arxiv_index_fts.py` and
restart uvicorn so the cached connection picks up the new file.

Scripts assume they are run from the repo root — they use relative paths like `./data/<source>/<source>.db` or `data/<source>/<source>.db`. `cd` to the repo root first.

## Running the API

```bash
source .venv/bin/activate
pip install -r requirements.txt          # first time only
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Listens on `0.0.0.0:8002` so the Tailscale interface picks it up; access is gated by Tailscale ACLs (no app-level auth). Other local uvicorn apps already occupy 8000 and 8001 — keep this on 8002.

`GET /health` returns per-database status. Routers:

- `/arxiv/papers`, `/arxiv/papers/{paper_id:path}`, `/arxiv/papers/{paper_id:path}/content` — list (filter by `primary_category` exact, `category` substring, `submitted_year`, `submitted_from`/`submitted_to` ISO dates, `author` substring, `has_html`, `q` full-text on title+abstract; sort by `submitted_desc` / `submitted_asc` / `updated_desc` / `relevance`), metadata, and raw HTML body. `q` joins through the `papers_fts` FTS5 table (same FTS5 syntax as openalex). `:path` converter handles old-style ids like `cond-mat/0204015`. **Error model departs from openalex:** missing `papers_fts` or unreadable `arxiv.db` returns 503 with the script name to run; only bad FTS syntax returns 400.
- `/factbook/countries`, `/factbook/countries/{id}` — list + detail (filter by `region`)
- `/openalex/works`, `/openalex/works/{short_id}` — list (filter by `year`, `cited_by_min`, `cited_by_max`, `venue`, `author` substring, `q` full-text on title+abstract; sort by `cited_by_count_desc` / `year_desc` / `year_asc` / `relevance`) + detail. `author` joins through the normalized `work_authors` / `authors` tables. `q` joins through the `works_fts` FTS5 table and accepts FTS5 syntax (`"phrase"`, `term*`, `a OR b`, `a NOT b`); when `q` is set the default sort becomes `relevance` (bm25). `short_id` is the `W…` suffix; the full `https://openalex.org/<id>` URL is reconstructed server-side.
- `/gutenberg/texts`, `/gutenberg/texts/{id}`, `/gutenberg/texts/{id}/content` — list (filter by `title` / `author` substring, `language` exact), metadata, and streamed raw `.txt`.

All list endpoints paginate with `limit` (default 50, max 200) and `offset`, returning `{items, total, limit, offset}`. API is read-only — downloader / indexer scripts are the only write path.

## Per-script notes

- **`arxiv_index_fts.py`** — Builds the `papers_fts` FTS5 virtual table over `papers.title` + `papers.abstract` with the `porter unicode61` tokenizer (matches openalex's choice). External-content table — the index lives in `papers_fts` but the original text stays in `papers` (no duplication). Drop+rebuild on each run (~0.1 s at the current ~1.2k-row scale, adds <1 MB to the DB file). Required after every refresh-copy of `data/arxiv/arxiv.db` from `local_wikipedia` for `/arxiv/papers?q=` to return anything. No `CREATE INDEX` calls — the two existing indexes on `papers` (`idx_papers_primary_cat`, `idx_papers_submitted`) ride along with the copy.
- **`factbook_download.py`** — Clones `github.com/factbook/factbook.json` to `/tmp/factbook_json`, walks the per-region directories, and inserts each country as one row (with the full JSON blob in a `data` column) into a `countries` table at `data/factbook/factbook.db`. Temp clone is removed on success.
- **`openalex_download.py`** — Paginates the OpenAlex `/works` API filtering by `cited_by_count` and reconstructs abstracts from the inverted-index format the API returns. Uses the OpenAlex "polite pool" (`mailto=` param) so changing the `EMAIL` constant matters for rate limiting. Cursor pagination, ~10 req/sec. Author display names are joined with `", "` into the `works.authors` column — the normalized author tables are built by `openalex_normalize_authors.py`.
- **`openalex_normalize_authors.py`** — One-shot backfill: creates `authors(id, display_name)` and `work_authors(work_id, author_id, position)` in `data/openalex/openalex.db` by splitting `works.authors` on `", "`. Re-runnable: clears `work_authors` first; keeps the `authors` table to avoid churning IDs. Required after `openalex_download.py` for `/openalex/works?author=` to return anything. **Known low-impact limitation (~0.08%, ~220 of 268k works):** credentialed-suffix names like `"Smith, Jr."`, `"Jones, M.D."`, `"Doe, PhD"`, `"Foo, III"` get fragmented into 2–3 phantom rows. The proper fix is a re-download using OpenAlex's authorship IDs, deferred.
- **`openalex_index_fts.py`** — Builds the `works_fts` FTS5 virtual table over `works.title` + `works.abstract` with the `porter unicode61` tokenizer. External-content table — the index lives in `works_fts` but the original text stays in `works` (no duplication). Drop+rebuild on each run (~20 s, ~150 MB added to the DB). Required after `openalex_download.py` for `/openalex/works?q=` to return anything.
- **`gutenberg_download.sh`** — Single rsync line that runs on a remote host (`pop-os`) via SSH and pulls `.txt` files from the ibiblio Gutenberg mirror. Not self-contained — requires that SSH alias to resolve.
- **`gutenberg_index.py`** — Walks `data/gutenberg/` for canonical `<id>-0.txt` files, joins them against the official Project Gutenberg catalog CSV (`gutenberg.org/cache/epub/feeds/pg_catalog.csv`) for title/author/language/release-date metadata, and writes `data/gutenberg/gutenberg.db`. Indexes on `author`, `title`, `language`. Re-runnable (`INSERT OR REPLACE`); skips `old/` retired versions. Required before the `/gutenberg` API routes work.
- **`kaggle_download.sh`** — Template script: `KAGGLE_USERNAME`, `KAGGLE_API_KEY`, and `DATASET` must be filled in before use. Writes credentials to `~/.kaggle/kaggle.json` and shells out to the `kaggle` CLI (pip-installed on demand).

## API layout (`api/`)

- `api/main.py` — FastAPI app, mounts the four routers, exposes `/health`.
- `api/db.py` — opens each SQLite DB read-only via the `file:...?mode=ro` URI form. Connections are module-level singletons and shared across threads (read-only, so safe). `GUTENBERG_ROOT` is the on-disk root the gutenberg content endpoint streams from.
- `api/models.py` — Pydantic response models, plus a generic `Page[T]` wrapper used by every list endpoint.
- `api/routers/{arxiv,factbook,openalex,gutenberg}.py` — one router per datasource. SQL is inline; the routers are intentionally thin.

Indexes that the list endpoints rely on are created by the **downloader / indexer scripts**, not the API (read-only mode forbids `CREATE INDEX`). If you add a new filter that needs an index, add the `CREATE INDEX IF NOT EXISTS` to the relevant downloader and re-run it (or apply it once by hand to the existing DB file).

## Conventions

- Each new source gets its own script in `scripts/` and writes to `data/<source>/`.
- SQLite tables use `INSERT OR REPLACE` / `INSERT OR IGNORE` so scripts are safe to re-run incrementally.

## Working rules

- Always ask clarifying questions before starting a coding task.
- Always pause and confirm before committing to git.
- Speak simply in plain terms — avoid unnecessary software jargon.
- Python code follows PEP 8, with docstrings, code comments, and type hints.
- Prefer the standard library over third-party packages whenever practical.
- Structure code in small, modular pieces with clear responsibilities.
- Follow the DRY principle — factor out repetition rather than copy-pasting.
- Think about security at both the planning and implementation stages (secrets handling, input validation, safe file/network use).
