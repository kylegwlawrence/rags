# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Personal collection of one-shot downloader scripts that fetch public datasets into local SQLite databases or plain files under `data/<source>/`, plus a small read-only FastAPI app (`api/`) that exposes them over the Tailscale network. Each downloader script is independent — there is no shared library, build system, or test suite. The `data/` directory is gitignored; only the scripts and the API are tracked.

## Running scripts

A Python venv lives at `.venv/`. Activate before running Python scripts:

```bash
source .venv/bin/activate
python scripts/arxiv_index_fts.py             # builds the papers_fts FTS5 index over data/arxiv/arxiv.db
python scripts/arxiv_index_rag.py             # builds data/arxiv/arxiv_rag.db (chunks + FTS + sqlite-vec)
python scripts/factbook_download.py
python scripts/factbook_index_rag.py          # builds data/factbook/factbook_rag.db from countries JSON
python scripts/openalex_download.py
python scripts/openalex_normalize_authors.py  # backfills authors + work_authors tables
python scripts/openalex_index_fts.py          # builds the works_fts FTS5 index
python scripts/openalex_index_rag.py          # builds data/openalex/openalex_rag.db (top-5k by citation count)
python scripts/gutenberg_index.py             # builds data/gutenberg/gutenberg.db from mirror + PG catalog
python scripts/gutenberg_index_rag.py         # builds data/gutenberg/gutenberg_rag.db (chunks + FTS + sqlite-vec)
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

`GET /health` returns per-database status. HTTP 503 if any database is broken; 200 otherwise. Routers:

- `/arxiv/papers`, `/arxiv/papers/{paper_id:path}`, `/arxiv/papers/{paper_id:path}/content` — list (filter by `primary_category` exact, `category` substring, `submitted_year`, `submitted_from`/`submitted_to` ISO dates, `author` substring, `has_html`, `q` full-text on title+abstract; sort by `submitted_desc` / `submitted_asc` / `updated_desc` / `relevance`), metadata, and raw HTML body. `q` joins through the `papers_fts` FTS5 table (same FTS5 syntax as openalex). `:path` converter handles old-style ids like `cond-mat/0204015`. Missing `papers_fts` or unreadable `arxiv.db` returns 503 with the script name to run; only bad FTS syntax returns 400.
- `/arxiv/chunks` — hybrid (FTS5 + sqlite-vec) chunk search over `arxiv_rag.db`. Params: `q` (required), `top_k` (default 20, max 100), `candidate_k` (default 50, max 200). Response: `{items, used_dense, top_k, candidate_k}` — **not** `Page[T]`; RRF doesn't paginate. `used_dense=false` means Ollama was unreachable and the body is sparse-only. 400 on empty `q`; 503 when `arxiv_rag.db` or its `chunks_fts`/`chunks_vec` tables are missing.
- `/openalex/chunks` — same shape as `/arxiv/chunks` but over `openalex_rag.db` (sampled to the top-5000 most-cited works by `cited_by_count`; embedding the full 268k is deferred — see `docs/retros/2026-05-18-openalex-phase-2b-rag.md` for the scope decision).
- `/factbook/chunks` — same shape, over `factbook_rag.db`. Each country's nested JSON is rendered as section-tagged markdown (one `##` heading per top-level section: Introduction, Geography, Economy, etc.) and chunked with `rag.chunker.chunk_markdown` so chunks carry their section name. `doc_id` is the factbook country code (`us`, `af`, …).
- `/gutenberg/chunks` — same shape, over `gutenberg_rag.db`. Books are read from disk (`GUTENBERG_ROOT / texts.path`), Project Gutenberg start/end banners stripped, then paragraph-chunked with `rag.chunker.chunk_doc` at `chunk_size=2000`. `doc_id` is the Gutenberg integer id (as a string). Section is always None — `.txt` corpus has no markdown structure.
- `/factbook/countries`, `/factbook/countries/{id}` — list + detail (filter by `region`)
- `/openalex/works`, `/openalex/works/{short_id}` — list (filter by `year`, `cited_by_min`, `cited_by_max`, `venue`, `author` substring, `q` full-text on title+abstract; sort by `cited_by_count_desc` / `year_desc` / `year_asc` / `relevance`) + detail. `author` joins through the normalized `work_authors` / `authors` tables. `q` joins through the `works_fts` FTS5 table and accepts FTS5 syntax (`"phrase"`, `term*`, `a OR b`, `a NOT b`); when `q` is set the default sort becomes `relevance` (bm25). Missing `works_fts` or unreadable `openalex.db` returns 503 with the script name to run; only bad FTS syntax returns 400. `short_id` is the `W…` suffix; the full `https://openalex.org/<id>` URL is reconstructed server-side.
- `/gutenberg/texts`, `/gutenberg/texts/{id}`, `/gutenberg/texts/{id}/content` — list (filter by `title` / `author` substring, `language` exact), metadata, and streamed raw `.txt`.

All list endpoints paginate with `limit` (default 50, max 200) and `offset`, returning `{items, total, limit, offset}`. API is read-only — downloader / indexer scripts are the only write path.

## Reload model

The API caches read-only SQLite connections at module load (`api/db.py`). After any downloader or indexer rewrites a `data/<source>/*.db` file, restart `uvicorn` for the cached handles to reopen against the new file. **This is the supported reload mechanism.** Per-script notes below repeat the reminder where it matters. Other approaches considered (per-request watermark invalidation in `_meta`; an admin `POST /admin/reload` endpoint) were rejected — both have higher cost than benefit for a personal hobbyist API. See `docs/retros/2026-05-18-phase-2-overall-retro.md` for the disposition decision.

## Per-script notes

- **`arxiv_index_fts.py`** — Builds the `papers_fts` FTS5 virtual table over `papers.title` + `papers.abstract` with the `porter unicode61` tokenizer (matches openalex's choice). External-content table — the index lives in `papers_fts` but the original text stays in `papers` (no duplication). Drop+rebuild on each run (~0.1 s at the current ~1.2k-row scale, adds <1 MB to the DB file). Required after every refresh-copy of `data/arxiv/arxiv.db` from `local_wikipedia` for `/arxiv/papers?q=` to return anything. No `CREATE INDEX` calls — the two existing indexes on `papers` (`idx_papers_primary_cat`, `idx_papers_submitted`) ride along with the copy.
- **`arxiv_index_rag.py`** — Builds `data/arxiv/arxiv_rag.db` (chunks + FTS5 + sqlite-vec) by chunking each paper's title+abstract and embedding with local Ollama (`nomic-embed-text:v1.5`, 768d). Title and abstract are HTML-stripped and whitespace-normalised by `rag.cleaner` before chunking. Re-runnable: skips papers whose `oai_datestamp` (or content hash fallback) + `CLEANER_VERSION` matches the previously-stored `docs_meta.version`. Detects legacy upstream schema (`paper_chunks*` from `local_wikipedia`) or embed model/dim mismatch and rebuilds from scratch. Phase 2a embeds title+abstract only; full-HTML chunking is deferred to Phase 3. CLI flags: `--limit`, `--reset`, `--batch`, `--ollama-url`, `--chunk-size` (default 1500), `--max-chunk-size` (default 1800). **Restart uvicorn after this runs** — the cached connection in `api/db.py` still points at the previous file.
- **`openalex_index_rag.py`** — Builds `data/openalex/openalex_rag.db` over the top-N most-cited OpenAlex works (default 5000 via `--limit`). Title and abstract are HTML-stripped and entity-decoded by `rag.cleaner` before chunking (the inverted-index reconstruction in `openalex_download.py` leaves `&amp;` / `<a>` in raw text). Same shared `rag/` machinery as the arxiv indexer; same embed model + 768d; same incremental version-hash skip (now suffixed with `CLEANER_VERSION`); same `--reset`/`--batch`/`--ollama-url` flags plus `--chunk-size` (default 1500) and `--max-chunk-size` (default 1800). Version key is a content hash of `(title, abstract)` since OpenAlex doesn't expose per-work `updated_at` in the current schema. Embed runtime depends on the chunker — re-measure before quoting an estimate (see WORK.md §1.10). Restart uvicorn after.
- **`factbook_index_rag.py`** — Builds `data/factbook/factbook_rag.db` from the nested `countries.data` JSON. Each country becomes one Doc; the JSON is rendered as section-tagged markdown (one `##` per top-level section). Every JSON leaf string is HTML-stripped and whitespace-normalised by `rag.cleaner` during the walk — the source JSON has embedded `<br>` and `<p>` tags that previously leaked into 26.8% of chunks. Chunked with `rag.chunker.chunk_markdown` (passed via `chunk_fn` to the shared indexer) so chunks preserve `Geography` / `Economy` / etc. labels in the `section` column (the `##` marker never reaches the chunk body). Version key is a SHA-256 of the JSON blob plus `CLEANER_VERSION`. CLI: `--chunk-size` (default 1000 — factbook is dense key:value), `--max-chunk-size` (default 1200), plus the standard `--limit`/`--reset`/`--batch`/`--ollama-url`. **Source-data caveat:** some factbook keys repeat themselves in their text values (e.g. key `"improved: urban"` with text `"urban: 99% ..."`); this produces `"improved: urban: urban: 99% ..."` lines and is left as-is.
- **`gutenberg_index_rag.py`** — Builds `data/gutenberg/gutenberg_rag.db` by reading `.txt` book bodies from `data/gutenberg/<path>`, stripping Project Gutenberg start/end banners (now covering the canonical `*** START/END OF ... ***` plus older Small-Print variants and bare-prose footers, with a defensive line-level scrub for any remaining "Project Gutenberg" mention) and inline markdown emphasis (`**`), then chunking with the default `chunk_doc`. CLI: `--language` (default `en`), `--limit` (default 100), `--chunk-size` (default 2000 — narrative prose is dense), `--max-chunk-size` (default 2400). Version key is `{size_bytes}-{sha256_prefix_of_first_4kb_and_last_4kb}-{CLEANER_VERSION}` — mtime is unreliable because the gutenberg rsync mirror can touch every file. Embed runtime depends on the corpus selected — re-measure before quoting an estimate.
- **`factbook_download.py`** — Clones `github.com/factbook/factbook.json` to `/tmp/factbook_json`, walks the per-region directories, and inserts each country as one row (with the full JSON blob in a `data` column) into a `countries` table at `data/factbook/factbook.db`. Temp clone is removed on success.
- **`openalex_download.py`** — Paginates the OpenAlex `/works` API filtering by `cited_by_count` and reconstructs abstracts from the inverted-index format the API returns. Uses the OpenAlex "polite pool" (`mailto=` param) so changing the `EMAIL` constant matters for rate limiting. Cursor pagination, ~10 req/sec. Author display names are joined with `", "` into the `works.authors` column — the normalized author tables are built by `openalex_normalize_authors.py`.
- **`openalex_normalize_authors.py`** — One-shot backfill: creates `authors(id, display_name)` and `work_authors(work_id, author_id, position)` in `data/openalex/openalex.db` by splitting `works.authors` on `", "`. Re-runnable: clears `work_authors` first; keeps the `authors` table to avoid churning IDs. Required after `openalex_download.py` for `/openalex/works?author=` to return anything. **Known low-impact limitation (~0.08%, ~220 of 268k works):** credentialed-suffix names like `"Smith, Jr."`, `"Jones, M.D."`, `"Doe, PhD"`, `"Foo, III"` get fragmented into 2–3 phantom rows. The proper fix is a re-download using OpenAlex's authorship IDs, deferred.
- **`openalex_index_fts.py`** — Builds the `works_fts` FTS5 virtual table over `works.title` + `works.abstract` with the `porter unicode61` tokenizer. External-content table — the index lives in `works_fts` but the original text stays in `works` (no duplication). Drop+rebuild on each run (~20 s, ~150 MB added to the DB). Required after `openalex_download.py` for `/openalex/works?q=` to return anything.
- **`gutenberg_download.sh`** — Single rsync line that runs on a remote host (`pop-os`) via SSH and pulls `.txt` files from the ibiblio Gutenberg mirror. Not self-contained — requires that SSH alias to resolve.
- **`gutenberg_index.py`** — Walks `data/gutenberg/` for canonical `<id>-0.txt` files, joins them against the official Project Gutenberg catalog CSV (`gutenberg.org/cache/epub/feeds/pg_catalog.csv`) for title/author/language/release-date metadata, and writes `data/gutenberg/gutenberg.db`. Indexes on `author`, `title`, `language`. Re-runnable (`INSERT OR REPLACE`); skips `old/` retired versions. Required before the `/gutenberg` API routes work.

### Re-indexing after a CLEANER_VERSION bump

Every per-source `Doc.version` ends with `-vN` where `N` is `rag.cleaner.CLEANER_VERSION`. Bumping that constant (or any change to the cleaning behaviour that warrants invalidation) makes every previously-stored `docs_meta.version` mismatch on the next indexer run, so every doc gets re-chunked and re-embedded. The `*_index_rag.py` scripts are idempotent and resumable — interrupted runs pick up cleanly. Restart uvicorn after each.

**Measured runtimes (v1 cleaner, 2026-05-19, local Ollama, nomic-embed-text:v1.5):** sampled chunks-per-doc on 30 docs (5 for gutenberg) and timed three 32-batch embed calls at realistic ~1600-char content. Per-chunk latency averaged ~1.4 s (range 1.0-1.9 s) — cold-start trials are slower.

- `arxiv_index_rag.py` — ~1.3 chunks/doc × 1238 papers = ~1.6k chunks → **~25-40 min**
- `openalex_index_rag.py` (`--limit 5000`) — ~1.7 chunks/doc × 5000 = ~8.5k chunks → **~2.5-3 h**
- `factbook_index_rag.py` — ~40 chunks/country × 261 = ~10k chunks → **~3-4 h**
- `gutenberg_index_rag.py` (`--limit 100`) — median ~136 chunks/book × 100 = ~14k chunks → **~4-5 h** (median anchor; mean is skewed by mega-corpora like the Bible at 2,354 chunks)

These numbers come from a 30-doc per-source sample on the current chunker — re-measure on a representative sample before quoting a new estimate after any chunker change (WORK.md §1.10).

## API layout (`api/`)

- `api/main.py` — FastAPI app, mounts the four routers, exposes `/health`.
- `api/db.py` — opens each SQLite DB read-only via the `file:...?mode=ro` URI form. Connections are module-level singletons and shared across threads (read-only, so safe). `GUTENBERG_ROOT` is the on-disk root the gutenberg content endpoint streams from. `_connect_ro_with_vec` additionally loads sqlite-vec for the per-source `_rag.db` files.
- `api/models.py` — Pydantic response models, plus a generic `Page[T]` wrapper used by every list endpoint. `Chunk` and `ChunksResponse` are the RAG-specific shapes (not paginated).
- `api/routers/{arxiv,factbook,openalex,gutenberg}.py` — one router per datasource. SQL is inline; the routers are intentionally thin.
- `rag/` — shared RAG primitives used by both the API and the indexer scripts. `chunker.py` (recursive boundary-aware splitting via `langchain-text-splitters` with a hard-cap post-pass; `chunk_doc` for prose, `chunk_markdown` for sections), `cleaner.py` (HTML stripping via `beautifulsoup4`, markdown-syntax stripping that preserves heading text, whitespace normalisation; exposes `CLEANER_VERSION` which every extractor appends to its `Doc.version`), `embedder.py` (Ollama HTTP, locked to `nomic-embed-text:v1.5` at 768d; `OLLAMA_URL` env var, default `http://localhost:11434`), `retriever.py` (RRF over FTS5 + sqlite-vec with sparse-only fallback when Ollama is down), `schema.py` (uniform DDL for `chunks`/`chunks_fts`/`chunks_vec`/`docs_meta`/`_meta` and the `connect_rag` writer-side opener).
- `tests/` — pytest smoke suite. One happy-path per route + 400/503/used_dense=false cases for each `/<source>/chunks` endpoint. Run with `pytest`.

Indexes that the list endpoints rely on are created by the **downloader / indexer scripts**, not the API (read-only mode forbids `CREATE INDEX`). If you add a new filter that needs an index, add the `CREATE INDEX IF NOT EXISTS` to the relevant downloader and re-run it (or apply it once by hand to the existing DB file).

## Conventions

- Each new source gets its own script in `scripts/` and writes to `data/<source>/`.
- SQLite tables use `INSERT OR REPLACE` / `INSERT OR IGNORE` so scripts are safe to re-run incrementally.

## Working rules

- Always ask clarifying questions before starting a coding task.
- Always pause and confirm before committing to git.
- Speak simply in plain terms — avoid unnecessary software jargon.
- Python code follows PEP 8, with docstrings, code comments, and type hints.
- Prefer the standard library over third-party packages whenever practical. Two intentional deviations live in `rag/`: `langchain-text-splitters` for the chunker (a stdlib regex chunker was previously producing 8-38% mid-word cuts across sources) and `beautifulsoup4` for HTML stripping (the factbook JSON had embedded HTML in 26.8% of leaves).
- Structure code in small, modular pieces with clear responsibilities.
- Follow the DRY principle — factor out repetition rather than copy-pasting.
- Think about security at both the planning and implementation stages (secrets handling, input validation, safe file/network use).
