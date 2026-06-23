# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Personal collection of one-shot downloader scripts that fetch public datasets into local SQLite databases under `data/<source>/`, plus a read-only FastAPI app (`api/`) exposed over the Tailscale network. Scripts are independent one-shots (no build system); `rag/` holds the shared chunk/embed logic used by the scripts and the API, and `tests/` is a pytest smoke suite. `data/` is gitignored.

## Running scripts

Activate the venv first; run all scripts from the repo root:

```bash
source .venv/bin/activate
python scripts/arxiv/arxiv_ingest.py                # OAI-PMH metadata → data/arxiv/arxiv.db
python scripts/arxiv/arxiv_download.py              # HTML bodies for papers
python scripts/arxiv/arxiv_normalize_authors.py     # one-shot backfill for legacy DBs only
python scripts/arxiv/arxiv_index_fts.py             # papers_fts FTS5 index (--db /datasets/arxiv/arxiv.db)
python scripts/arxiv/arxiv_index_rag.py             # data/arxiv/arxiv_rag.db
python scripts/factbook/factbook_download.py
python scripts/factbook/factbook_index_rag.py       # data/factbook/factbook_rag.db
python scripts/openalex/openalex_download.py
python scripts/openalex/openalex_fetch_bodies.py    # fetch OA PDFs → data/openalex/bodies/ (feed into pdfs scripts)
python scripts/openalex/openalex_normalize_authors.py  # required for ?author= filter
python scripts/openalex/openalex_index_fts.py       # works_fts FTS5 index
python scripts/openalex/openalex_index_rag.py       # data/openalex/openalex_rag.db (top-5k)
python scripts/gutenberg/gutenberg_index.py         # data/gutenberg/gutenberg.db
python scripts/gutenberg/gutenberg_index_rag.py     # data/gutenberg/gutenberg_rag.db
python scripts/gutenberg/gutenberg_download.py      # rsync from ibiblio; --language (default en), --dry-run
python scripts/simplewiki/simplewiki_download.py
python scripts/simplewiki/simplewiki_parse.py
python scripts/simplewiki/simplewiki_index_categories.py  # page_categories table (--db; also enwiki.db)
python scripts/simplewiki/simplewiki_index_rag.py   # data/simplewiki/simplewiki_rag.db
python scripts/python_docs/python_docs_download.py
python scripts/python_docs/python_docs_index_fts.py
python scripts/python_docs/python_docs_index_rag.py # data/pydocs/python_docs_rag.db
python scripts/loc/loc_download.py
python scripts/loc/loc_newspapers_download.py
python scripts/loc/loc_books_marc.py
python scripts/loc/loc_fetch_bodies.py                # fetch whole-item PDFs → data/loc/bodies/ (feed into pdfs scripts)
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
python scripts/ecfr/ecfr_index_rag.py                 # data/ecfr/ecfr_rag.db (full ~509k chunks ≈ 8 days)
python scripts/pdfs/pdfs_ingest.py                    # PDFs in data/pdfs/incoming/ → data/pdfs/pdfs.db
python scripts/pdfs/pdfs_index_fts.py                 # pages_fts FTS5 index
python scripts/pdfs/pdfs_index_rag.py                 # data/pdfs/pdfs_rag.db (page-aware chunks)
python scripts/openstax/openstax_download.py          # OpenStax osbooks-* GitHub repos → data/openstax/openstax.db
python scripts/openstax/openstax_index_fts.py         # sections_fts FTS5 index
python scripts/openstax/openstax_index_rag.py         # data/openstax/openstax_rag.db
python scripts/federal_register/federal_register_download.py    # FR documents (1994–present) → data/federal_register/federal_register.db
python scripts/federal_register/federal_register_index_fts.py   # documents_fts FTS5 index
python scripts/federal_register/federal_register_index_rag.py   # data/federal_register/federal_register_rag.db
python scripts/geonames/geonames_download.py          # GeoNames allCountries (~13M places) → data/geonames/geonames.db
python scripts/geonames/geonames_index_fts.py         # places_fts FTS5 index
python scripts/github_readmes/github_readmes_download.py    # READMEs from 15 awesome-lists → data/github/readmes.db
python scripts/github_readmes/github_readmes_prune.py       # drop low-quality READMEs (dry-run by default; --execute)
python scripts/github_readmes/github_readmes_index_fts.py   # readmes_fts FTS5 index
python scripts/github_readmes/github_readmes_index_rag.py   # data/github/github_readmes_rag.db
python scripts/wikinews/wikinews_download.py                # English Wikinews archive dump → data/wikinews/dumps/
python scripts/wikinews/wikinews_parse.py                   # dump → data/wikinews/wikinews.db (FTS built inline)
python scripts/simplewiki/simplewiki_index_categories.py --db data/wikinews/wikinews.db  # page_categories table
python scripts/wikinews/wikinews_index_rag.py               # data/wikinews/wikinews_rag.db
python scripts/justice_canada/download.py                   # laws-lois-xml corpus → data/justice_canada/laws-lois-xml/
python scripts/justice_canada/parse.py                      # XML → data/justice_canada/justice_canada.db (acts + regulations)
python scripts/justice_canada/index_fts.py                  # acts_fts + regulations_fts FTS5 indexes
```

## Running the API

```bash
source .venv/bin/activate
pip install -r requirements.txt   # first time only
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Port 8002 is fixed (8000/8001 occupied). Tailscale ACLs gate access; no app-level auth. `GET /health` returns per-DB status (503 if any DB broken).

**Reload:** restart uvicorn after any indexer/downloader run — read connections are cached at module load. Exceptions needing no restart (live write paths): `/embed` routes write via a fresh RW connection (WAL makes committed rows visible to the cached reader at once); `POST /arxiv/papers/{id}/download` and `POST /sec_edgar/filings/{accession}/download` do an in-place single-row UPDATE via `db.connect_rw` (same file, no inode swap, so the cached read-only connection sees the row next query even though those DBs aren't WAL).

## Frontend & deployment

This repo is the **backend only** (API + data + ingest scripts) and no longer serves any UI — there is no `/ui` mount in `api/main.py`. The frontend is its own repo, **`datasets_frontend`**, deployed on **raspberrypi6**: a small FastAPI host (`server.py` + `static/`) that serves the single-page UI under `/ui/` and reverse-proxies everything else to the backend (`DATASETS_BACKEND_URL`, default pop-os Tailscale IP). It binds the Pi's Tailscale IP on port 8002, so `http://raspberrypi6:8002/` is the single user-facing URL. Edit the UI there; its own README covers the systemd deploy.

**Where scripts and data live:** everything in this repo runs on **pop-os only** — the `scripts/` downloaders/indexers, the `data/<source>/*.db` + `/datasets/arxiv/arxiv.db` files they produce, the API that reads them, and the live `rag/` embed/download write paths. raspberrypi6 runs only the `datasets_frontend` proxy (no copy of this data, no `/datasets` mount). Run all `scripts/` on pop-os, then restart uvicorn there.

## API routes

All list endpoints: `limit` (default 50, max 200) + `offset` → `{items, total, limit, offset}`. Chunk endpoints: `q` (required), `top_k`, `candidate_k` → `{items, used_dense, top_k, candidate_k}` (RRF, not paginated). Missing FTS table → 503 with script name; bad FTS syntax → 400; Ollama down → sparse-only (`used_dense=false`). `?embedded=` filters by chunk presence; `sort=relevance` needs `q` (else document/date order). Routes below list the path family + query params; deep behavior is under "Script notes".

- `/arxiv/papers`, `/{id:path}`, `/{id:path}/content`, `POST /{id:path}/download`, `POST /{id:path}/embed`, `/arxiv/chunks`, `/arxiv/categories` — served from the monolithic `arxiv.db` (`api/db.py` `arxiv()`); plain SQL. `sort=relevance` is `bm25(papers_fts)`. `/categories` is the `{code: description}` taxonomy read (cached) from `data/arxiv/categories.csv` (frontend labels the Category fields; 503 if missing). `POST .../download` fetches one paper's LaTeXML HTML on demand (`rag.arxiv_fetch.fetch_paper_html`) and writes `html_content`; a 404 records `download_status='no_html'` (200). Needs `DATASETS_EMAIL`.
- `/openalex/works` (`?q=`, `?year=`, `?cited_by_min/max=`, `?venue=`, `?domain=`, `?field=`, `?author=`, `?embedded=`, `?sort=`), `/{short_id}`, `POST /{short_id}/download`, `/openalex/chunks` — `?domain=`/`?field=` exact-match the work's primary-topic hierarchy. `POST .../download` fetches one work's OA PDF (`rag.openalex_fetch.fetch_work_pdf`) to `data/openalex/bodies/{short_id}.pdf` for the `pdfs` pipeline — writes only a `body_status` row (no body in openalex.db); `status='no_pdf'` (200) when none. Needs `DATASETS_EMAIL`.
- `/factbook/countries`, `/{id}`, `/factbook/chunks`
- `/gutenberg/texts`, `/{id}`, `/{id}/content`, `/gutenberg/chunks`
- `/simplewiki/articles` (`?q=`, `?title=`, `?namespace=`, `?category=`, `?embedded=`), `/{page_id}`, `/{page_id}/content`, `POST /{page_id}/embed`, `/simplewiki/resolve`, `/simplewiki/chunks`, `/simplewiki/categories` — `/content` renders wikitext to **HTML** via `rag.wiki_render` (KaTeX math, infoboxes, tables, refs). `/resolve?title=` maps an exact `[[wikilink]]` to its article (index-backed, first-letter-capitalised fallback; 404 if none) for in-app navigation. `?category=` is a case-insensitive substring on the normalized name; `/categories` lists distinct categories with counts (`?q=`, `?sort=count|name`). Both back onto the `page_categories` table (503 if not built).
- `/enwiki/articles` (`?q=`, `?title=`, `?namespace=`), `/{page_id}`, `/{page_id}/content`, `POST /{page_id}/embed`, `/enwiki/resolve`, `/enwiki/chunks`, `/enwiki/doc-chunks` — local `enwiki.db` (`api/db.py` `enwiki()`); same shape as simplewiki (HTML `/content`, `/resolve`). `?q=` is FTS5 **trigram** over title + body. Embedding (so `/chunks`) is on-demand only.
- `/pydocs/docs`, `/{doc_path:path}`, `/{doc_path:path}/content`, `/pydocs/chunks`
- `/sec_edgar/filings` (`?downloaded=`), `/{accession_number}`, `/{accession_number}/content`, `POST /{accession_number}/download`, `/sec_edgar/chunks` — list surfaces metadata-only filings; `?downloaded=` narrows to fetched/unfetched.
- `/worldbank/indicators` (`?q=`, `?topic=`), `/indicators/{id}`, `/indicators/{id}/values` (`?country=`, `?year=`), `/worldbank/countries`, `/countries/{id}/data` (`?topic=`, `?year=`)
- `/billstatus/bills` (`?q=`, `?congress=`, `?bill_type=`, `?sponsor=`, `?policy_area=`, `?subject=`, `?sort=`), `/{bill_id}`, `/{bill_id}/content` — `bill_id` is `{congress}-{TYPE}-{number}` (e.g. `118-HR-1234`). No RAG/chunks.
- `/ecfr/regulations` (`?q=`, `?title=`, `?part=`, `?embedded=`, `?sort=`), `/{reg_id}`, `/{reg_id}/content`, `POST /{reg_id}/embed`, `/ecfr/chunks` — one row per CFR section; `reg_id` is the int row id; `?q=` over heading + content. RAG via batch indexer or on-demand embed.
- `/openstax/books` (`?q=`, `?subject=`), `/{book_id}`; `/openstax/sections` (`?q=`, `?book_id=`, `?subject=`, `?embedded=`, `?sort=`), `/sections/{section_id:path}`, `/sections/{section_id}/content`, `POST /sections/{section_id}/embed`, `/openstax/chunks` (`?q=`, `?subject=`, `?book_id=`, `?chapter_number=`), `/openstax/doc-chunks` — `book_id` is the slug, a section id is `{book_id}/{module_id}`. `/content` is light Markdown with inline `\(…\)`/`\[…\]` LaTeX (KaTeX). On `/chunks`, `?subject=`/`?book_id=` are **repeatable** (OR), `?chapter_number=` single-value; they scope retrieval to a `section_id` allowlist, and each hit carries `book_id`/`subject`/`chapter_number`/`chapter_title`.
- `/pdfs/documents` (`?q=`, `?title=`, `?author=`, `?sort=`), `/{doc_id}`, `/{doc_id}/content`, `POST /{doc_id}/embed`, `/pdfs/chunks`, `/pdfs/doc-chunks` — one row per PDF (`doc_id` = filename stem). `?q=` over per-page text, rolled up to documents (best page's bm25). `/content` streams the original PDF inline. Chunked page by page, so a chunk's `section` is its page label (`"p. 42"`) and hits deep-link the viewer (`#page=N`).
- `/federal_register/documents` (`?q=`, `?type=`, `?agencies=`, `?publication_year=`, `?embedded=`, `?sort=`), `/{document_number}`, `/{document_number}/content`, `POST /{document_number}/embed`, `/federal_register/chunks`, `/federal_register/doc-chunks` — one row per FR document (1994–present). `?q=` over title + abstract; `?type=` exact (`Rule`/`Proposed Rule`/`Notice`), `?agencies=` substring. `/content` returns the abstract (falls back to excerpts).
- `/geonames/places` (`?q=`, `?country_code=`, `?feature_class=` (repeatable), `?feature_code=` (repeatable), `?min_population=`), `/{geonameid}`, plus `/geonames/feature_classes` and `/geonames/feature_codes` (`?feature_class=` repeatable) lookups — ~13M features. `?q=` over name + country_name + feature_description; default sort population-desc. **No RAG/chunks.** Always pass a filter; an open query scans all 13M rows.
- `/github/readmes` (`?q=`, `?owner=`, `?source_list=`, `?embedded=`, `?sort=`), `/{repo:path}`, `/{repo:path}/content`, `POST /{repo:path}/embed`, `/github/chunks`, `/github/doc-chunks` — one row per repo README from 15 "awesome" lists; `repo` is the `owner/name` slug. Only `status='fetched'` rows served. `?q=` over name + body, `?owner=` substring, `?source_list=` the discovering list. `/content` is raw README markdown.
- `/justice_canada/laws` (`?q=`, `?type=`, `?in_force=`, `?regulation_type=`, `?sort=`), `/{law_id:path}`, `/{law_id:path}/content` — consolidated Canadian acts + regulations served from one DB (two tables UNIONed). `law_id` is an act's chapter number or a regulation's instrument number. `?type=` is `acts`/`regulations`; `?q=` is FTS5 over title + body across **both** tables (each side joins its own `acts_fts`/`regulations_fts`, merged by `bm25` rank); `sort=relevance` requires `q`, else newest-first by `last_amended_date` (`sort=oldest` flips it). `/content` is Markdown plain text. No RAG/chunks.

Write paths: the on-demand `/embed` routes (→ `<source>_rag.db`), plus `POST /arxiv/.../download` (writes `html_content` onto `arxiv.db`), `POST /sec_edgar/.../download` (writes a body onto `sec_edgar.db`), and `POST /openalex/.../download` (writes a `body_status` row onto `openalex.db`; the PDF lands under `data/openalex/bodies/`). Everything else is read-only.

## Script notes

**Universal:** restart uvicorn after any indexer/downloader run. Every `*_index_fts.py` rebuilds that source's `<table>_fts` (porter, external-content unless noted) and is required for `?q=`; every `*_index_rag.py` writes `data/<source>/<source>_rag.db` and shares `--limit`/`--reset`/`--chunk-size`/`--max-chunk-size`/`--overlap`. Polite-pool downloaders (arxiv, openalex, sec_edgar, ecfr, federal_register) need `DATASETS_EMAIL`. One line per source below; chunk settings shown as size/max/overlap.

- **arxiv** — monolithic ~80 GB DB at `/datasets/arxiv/arxiv.db` (outside the repo; `/home` near-full), read-only via `api/db.py` `arxiv()`, no sharding; scripts default to `--db data/arxiv/arxiv.db` so pass `--db /datasets/arxiv/arxiv.db`. `arxiv_ingest.py` (OAI-PMH, 3 s/req; `--from`/`--until`/`--from-cache`/`--reset`), `arxiv_download.py` (HTML bodies; `--limit`/`--force`), `arxiv_normalize_authors.py` (pre-Phase-3 backfill, idempotent), `arxiv_index_rag.py` (global, not sharded; full HTML else abstract-only; 1500/1800/150).
- **factbook** — `factbook_download.py` clones `github.com/factbook/factbook.json` → `factbook.db`; `factbook_index_rag.py` (1000/1200/100).
- **openalex** — serves only OA *pointers*, never body text. `openalex_download.py` (`/works`; stores `is_oa`/`oa_status`/`oa_url`/`pdf_url`; upserts), `openalex_fetch_bodies.py` (standalone PDF fetcher, highest-cited first → `data/openalex/bodies/{short_id}.pdf` for `pdfs`; keeps only `%PDF-`; resumable via `body_status`; `rag/openalex_fetch.py` shared with download route; `--limit` 50/`--out-dir`/`--delay` 3 s/`--skip-errors`), `openalex_normalize_authors.py` (builds `authors`/`work_authors`, required for `?author=`), `openalex_index_rag.py` (top-5k by citation).
- **gutenberg** — `gutenberg_download.py` (PG catalog CSV + rsync from ibiblio; `--language` en/`all`, `--dry-run`), `gutenberg_index.py` (`.txt` + catalog → `gutenberg.db`), `gutenberg_index_rag.py` (`--language` en, `--limit` 100; 2000/2400/300), `gutenberg_archive.py` (tar+zstd shards `{0..9}` → `archives/{n}.tar.zst`, leaves originals; `--base-dir`/`--out-dir`/`--level`/`--threads`/`--folder`/`--force`).
- **simplewiki** — `simplewiki_download.py` (downloads + SHA-1 verifies), `simplewiki_parse.py` (bz2 XML → `simplewiki.db`; `--all-namespaces`), `simplewiki_index_categories.py` (builds `page_categories` from `[[Category:...]]` via `rag.wikitext.normalize_category`; idempotent; reusable for enwiki via `--db data/enwiki/enwiki.db`), `simplewiki_index_rag.py` (`--limit` 100 default, full 394k ≈ 700 h; 800/1000/100 — **keep in sync with `api/routers/simplewiki.py` `_CHUNK_SIZE`/`_MAX_CHUNK_SIZE`/`_OVERLAP`**).
- **enwiki** (local DB) — full ~263 GB `enwiki.db` (~19M rows) served directly read-only via `api/db.py` `enwiki()` (old raspberrypi6 proxy gone). `articles_fts` is a **trigram** index over title + body, prebuilt inside the file — no parse/index script here, so a missing table means restore the DB. No batch RAG indexer — on-demand via `POST /enwiki/articles/{page_id}/embed` (ENWIKI profile == SIMPLEWIKI); `enwiki_rag.db` ships empty. `scripts/enwiki/enwiki_remote_server.py` is the old pi service, reference only.
- **python_docs** — `python_docs_download.py` (pass a pinned `--python-version` e.g. `3.13`; generic `3` redirect fails for `.tar.bz2`), `python_docs_index_rag.py` (~513 pages).
- **loc** — `loc_download.py` (search API; `--format`/`--language`; resumes via `ingest_state`), `loc_newspapers_download.py` (Chronicling America; `--date-from`/`--date-to`), `loc_books_marc.py` (MARC bulk from `data/loc/raw/`; needs `pymarc`; not resumable), `loc_fetch_bodies.py` (standalone whole-item PDF fetcher via `{url}?fo=json`, skips video/audio/image-only, resumable via `body_status`, feeds `pdfs`; `--limit` 50/`--out-dir`/`--delay` 3 s/`--skip-errors`).
- **sec_edgar** — stores metadata + URLs only. `sec_edgar_download.py` (quarterly full-index, 1993–present; `--start-year`/`--end-year`/`--email`/`--reset`; resumes via `ingest_state`), `sec_edgar_fetch_bodies.py` (standalone; clean text in `body` for FTS+RAG + render HTML in `body_html`; default 10-K newest first `--limit 200`; `--accession` one filing always refetches, `--form-type`, `--reset-status`; `rag/sec_filing.py` shared with download route; refilling `body_html` never touches `body` so indexes stay valid).
- **worldbank** — `worldbank_download.py`: 21 topic-tagged indicator groups from Indicators API v2 (no key); `--start-year` 2021/`--reset`; resumable via `completed_indicators`; ~1–2 h full.
- **billstatus** — `billstatus_download.py`: GPO BILLSTATUS XML zips per Congress/bill-type → `bills` (PK `{congress}-{TYPE}-{number}`, 108th–present); `--congress-from`/`--congress-to` 119; resumable. `billstatus_index_fts.py` over title + summary + subjects; no RAG (summaries short).
- **eurlex / ceps** (the `ceps` downloader writes `data/eurlex/`) — `ceps_download.py` is the only EUR-Lex downloader: CEPS dataset (142k laws 1952–2019, frozen) from Harvard Dataverse (DOI `10.7910/DVN/0EGYWY`) → dynamically-typed `laws` table; full text in `act_raw_text` (no body fetch); `--download-dir`/`--reset`; `raw/` CSVs deletable once loaded; no updater past 2019. `eurlex_index_rag.py` over `laws.act_raw_text` (`chunk_doc`, flat prose; content-hash skip; `rag/eurlex.py` `build_doc` shared with live-embed; `eurlex_rag_extract.py` entry point).
- **ecfr** — current snapshot, no history. `ecfr_download.py` (ecfr.gov versioner API; all 50 titles, 35 reserved/empty → `regulations` one row/section; resumes via `ingest_state.completed_titles`). `ecfr_index_rag.py` (flat prose `chunk_doc`; `build_doc` in `rag/ecfr.py` shared with live-embed; `ecfr_rag_extract.py` entry point; 1000/1200/100; full corpus ~509k chunks ≈ 8 days — use `--limit` to index a subset).
- **pdfs** (drop-folder pipeline; also fed by openalex/loc bodies) — `pdfs_ingest.py` (scans `data/pdfs/incoming/`, per-page text via **pdfplumber** → `documents`+`pages`, `doc_id` = stem; originals stay; idempotent unless `--force`), `pdfs_index_fts.py` (`pages_fts` over `pages.text`, page-level; API rolls up to documents), `pdfs_index_rag.py` (**page-aware**: one Doc/PDF, chunked per page so `section` is its page label `"p. {n}"` for viewer deep-links `#page=N`; `build_doc`/`chunk_pdf` in `rag/pdfs.py` shared with live-embed; `pdfs_rag_extract.py` entry point).
- **openstax** — `openstax_download.py`: shallow+sparse `git clone` of `osbooks-*` repos (`collections/`+`modules/`+`media/`) → `books`/`chapters`/`sections`, deletes clone; bodies light Markdown (`##`/`###` + inline `\(…\)`/`\[…\]` LaTeX rebuilt from MathML by stdlib `rag/mathml.py`); all English shelves (70 books / 10 subjects / ~9.4k sections); `--repos`/`--work-dir`/`--keep-clones`/`--skip-images`; **editing `rag/mathml.py` → re-run download, then FTS, then re-embed** (LaTeX baked in at download). Images copy to `data/openstax/media/{repo}/` (repo-namespaced) and appear in bodies as Markdown links `![alt](/openstax/media/{repo}/file.jpg)`, served by the `/openstax/media` static mount; `--skip-images` → text-only. `openstax_index_rag.py` section-aware `chunk_markdown` (doc leads with title + objectives then body, chunk `section` = "Chapter — Section"); `build_doc` + parsers in `rag/openstax.py` shared with live-embed; `openstax_rag_extract.py` entry point.
- **federal_register** — `federal_register_download.py` (federalregister.gov API v2, 1994–present → `documents` PK `document_number`; `--year-from`/`--year-to`; resumable via `ingest_state`), `federal_register_index_rag.py` (renders Markdown Details/Abstract/Action/Excerpts, chunks on `##`; `build_doc` in `rag/federal_register.py`; `federal_register_rag_extract.py` entry point).
- **geonames** — one-line records, no RAG. `geonames_download.py` (`allCountries.zip` ~330 MB + lookups → `places` PK `geonameid`, ~13M; synthetic `sentence` per row; writes `feature_classes.csv`/`feature_codes.csv`; not resumable; `--download-dir`/`--limit`/`--reset`), `geonames_index_fts.py` (over name + country_name + feature_description; backfills `feature_description` on older DBs, needs `feature_codes.csv`).
- **github_readmes** — `github_readmes_download.py` (15 "awesome" lists → `readmes` PK `owner/name`; uses `GITHUB_TOKEN` if set; `--delay` 0.7 s/`--limit`), `github_readmes_prune.py` (deletes low-value READMEs — link-dumps/too-short/image-only/>40% non-English — syncs `_rag.db`, rebuilds FTS; **dry-run unless `--execute`**), `github_readmes_index_rag.py` (cleans + chunks on `##`, same link-dump filter; **`--limit` 100 default**; `github_readmes_rag_extract.py` entry point, Doc built inline — no shared `rag/<source>.py`).
- **wikinews** — static English Wikinews archive (~22k articles, closed May 2026). `wikinews_download.py` (fetches `enwikinews-*-pages-articles.xml.bz2` from dumps.wikimedia.org, SHA-1 verify, ~48 MB). `wikinews_parse.py` (streams XML → `wikinews.db`; `pub_date` from `{{date|Month DD, YYYY}}`; trigram FTS over title + body inline). Categories: `simplewiki_index_categories.py --db data/wikinews/wikinews.db`. `wikinews_index_rag.py` (full corpus feasible; **`--limit` 100 default**; same pipeline as simplewiki; `WIKINEWS` profile 800/1000/100). `/wikinews/articles` supports `?date_from=`/`?date_to=` (ISO), `?sort=date|relevance`, `?category=`, `?embedded=`; default newest-first.
- **justice_canada** — consolidated Canadian acts + regulations from the Justice Canada `laws-lois-xml` corpus. `download.py` (XML corpus → `data/justice_canada/laws-lois-xml/`; `--language` en/fr/both), `parse.py` (XML → `justice_canada.db`; `acts`/`regulations` tables, body Markdown via `rag/justice_canada.py` `body_to_markdown`; `INSERT OR REPLACE`; `--language`/`--type`/`--corpus-dir`). `index_fts.py` builds **two** external-content FTS5 tables — `acts_fts` (short_title + long_title + running_head + body), `regulations_fts` (short_title + long_title + enabling_authority + body); both required for `?q=`/`sort=relevance` (router joins per-side, merges by `bm25`). No RAG indexer.

### Re-indexing notes

- Chunker setting changes (`--overlap`/`--chunk-size`/`--max-chunk-size`) don't trigger re-index (content-based version key) — pass `--reset`.
- `CLEANER_VERSION` bump forces re-embed of all docs. Scripts are idempotent and resumable.

**Runtimes** (local Ollama, nomic-embed-text:v1.5, ~1.4 s/chunk): arxiv 1.2k papers → ~1.6k chunks 25–40 min · openalex 5k → ~8.5k chunks 2.5–3 h · factbook 261 → ~10k chunks 3–4 h · gutenberg 100 → ~14k chunks 4–5 h · simplewiki 100 → ~10 min · simplewiki full 394k → ~2M chunks ~700 h.

## API layout

- `api/main.py` — mounts routers, `/health`.
- `api/db.py` — read-only module-level SQLite connections; `connect_rag_rw` for live embed writes; `connect_rw` for the SEC live body-download write.
- `api/models.py` — `Page[T]` for list endpoints; `ChunksResponse` for RAG.
- `api/routers/` — one thin router per source; SQL inline. `api/_chunks.py` — shared chunks factory (400 empty `q`, 503 missing rag.db, sparse fallback). `api/_fts.py` — `translate_fts_errors` (missing table → 503, bad FTS5 → 400).
- `rag/` — `chunker.py` (`chunk_doc`/`chunk_markdown`), `cleaner.py` (`CLEANER_VERSION`), `embedder.py` (nomic-embed-text:v1.5 768d, `OLLAMA_URL`), `render.py` (arxiv HTML→md), `wikitext.py` (wikitext→md for embedding), `wiki_render/` (wikitext→**HTML** for the wiki Content view; KaTeX + `\ce{}`, infoboxes, reflists, wikitables; `convert_wikitext_to_html`), `sec_filing.py` (`extract_primary`), `arxiv_fetch.py` (`fetch_paper_html`), `openalex_fetch.py` (`fetch_work_pdf` + `body_status`), `retriever.py` (RRF), `retry.py`, `schema.py`, `indexer.py`.
- `tests/` — pytest smoke suite; run with `pytest`. Indexes are created by downloader/indexer scripts (API is read-only) — add `CREATE INDEX IF NOT EXISTS` to the relevant script when adding a filter.

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
