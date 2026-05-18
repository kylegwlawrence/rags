# datasets

A read-only FastAPI app that exposes a handful of public datasets — arXiv
papers, CIA World Factbook, OpenAlex works, and Project Gutenberg texts —
over the local network. Data is downloaded by the scripts in `scripts/` into
SQLite files under `data/<source>/`; the API simply reads from those files.

This README focuses on the API. For per-script details (download cadence,
indexer steps, known limitations) see `CLAUDE.md`.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

The API expects these files to already exist:

- `data/arxiv/arxiv.db` (with the `papers_fts` FTS5 index built by
  `scripts/arxiv_index_fts.py` — see `CLAUDE.md`)
- `data/arxiv/arxiv_rag.db` (chunks + FTS + sqlite-vec embeddings built by
  `scripts/arxiv_index_rag.py`; used by `/arxiv/chunks`)
- `data/factbook/factbook.db`
- `data/openalex/openalex.db` (with the `authors`, `work_authors`, and
  `works_fts` tables populated — see `CLAUDE.md` for the indexer order)
- `data/gutenberg/gutenberg.db` and the `.txt` corpus under `data/gutenberg/`

If a database is missing, the corresponding routes will 500 but the rest of
the app still serves. Use `/health` to check.

## Running

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Port `8002` is reserved for this app (8000 and 8001 are taken by other local
uvicorn apps on this machine). Binding to `0.0.0.0` is intentional so the
Tailscale interface picks it up. There is no app-level auth — access is gated
by Tailscale ACLs. Do not expose this port directly to the public internet.

OpenAPI docs are at `/docs`.

## Endpoints

All list endpoints return a `Page[T]` shape:

```json
{ "items": [...], "total": 1234, "limit": 50, "offset": 0 }
```

`limit` defaults to 50 and is capped at 200. `offset` defaults to 0.

### `GET /health`

Runs `SELECT 1` against each database connection and returns per-database
status plus a top-level `ok` boolean.

### ArXiv

- `GET /arxiv/papers` — list papers.
  - `primary_category` — exact match (e.g. `cs.CL`).
  - `category` — substring match against the whitespace-separated category
    list (loose: `cs.C` will match `cs.CL`).
  - `submitted_year` — year prefix on `submitted_date`.
  - `submitted_from`, `submitted_to` — ISO date range (inclusive) on
    `submitted_date`.
  - `author` — substring match against the raw JSON-encoded authors text
    (not normalized).
  - `has_html` — `true` only returns papers with downloaded HTML, `false`
    only returns those without.
  - `q` — full-text search over `title` + `abstract`. Accepts the same FTS5
    syntax as `/openalex/works?q=`. Malformed queries return 400; a missing
    `papers_fts` index returns 503 with the indexer script name in the detail.
  - `sort` — one of `submitted_desc` (default when `q` is not set),
    `submitted_asc`, `updated_desc`, `relevance` (default when `q` is set;
    requires `q`).
- `GET /arxiv/papers/{paper_id}` — one paper. Old-style ids with embedded
  slashes (e.g. `cond-mat/0204015`) are supported via FastAPI's `:path`
  converter.
- `GET /arxiv/papers/{paper_id}/content` — raw HTML body as
  `text/html; charset=utf-8`. 404 distinguishes paper-not-found from
  no-downloaded-HTML.
- `GET /arxiv/chunks` — hybrid (FTS5 + sqlite-vec) chunk search.
  - `q` (required) — natural-language query; empty/whitespace → 400.
  - `top_k` — final result count after RRF (default 20, max 100).
  - `candidate_k` — pool size from each side before merging (default 50, max 200).
  - Returns `{items, used_dense, top_k, candidate_k}`. `used_dense=false`
    means Ollama was unreachable and only sparse FTS results contributed —
    body is still useful. 503 if `arxiv_rag.db` or its index tables are
    missing.

### Factbook

- `GET /factbook/countries` — list countries (id, name, region).
  - `region` — exact match filter.
- `GET /factbook/countries/{id}` — one country, including the parsed factbook
  JSON blob under `data`.

### OpenAlex

- `GET /openalex/works` — list works.
  - `year` — exact year.
  - `cited_by_min`, `cited_by_max` — citation count bounds.
  - `venue` — exact venue match.
  - `author` — substring match against any author display name (joins through
    the normalized `work_authors` / `authors` tables).
  - `q` — full-text search over `title` + `abstract`. Accepts FTS5 syntax:
    bare words are ANDed, `"phrase"` matches phrases, `term*` is a prefix
    match, `a OR b` and `a NOT b` work as expected. Malformed queries return
    400.
  - `sort` — one of `cited_by_count_desc` (default when `q` is not set),
    `year_desc`, `year_asc`, `relevance` (default when `q` is set; requires
    `q`).
- `GET /openalex/works/{short_id}` — one work. `short_id` is the `W…` suffix
  (e.g. `W3038568908`); the full `https://openalex.org/<id>` URL is
  reconstructed server-side.

### Gutenberg

- `GET /gutenberg/texts` — list texts.
  - `title`, `author` — substring match.
  - `language` — exact match (e.g. `en`).
- `GET /gutenberg/texts/{id}` — metadata for one text.
- `GET /gutenberg/texts/{id}/content` — stream the raw `.txt` file as
  `text/plain; charset=utf-8`. Paths are resolved against `GUTENBERG_ROOT` and
  rejected if they escape the root.

## Layout

- `api/main.py` — app entrypoint, mounts the four routers and `/health`.
- `api/db.py` — opens each SQLite DB read-only (`file:...?mode=ro`) as a
  module-level singleton connection. `_connect_ro_with_vec` additionally
  loads the sqlite-vec extension for the per-source `_rag.db` files.
- `api/models.py` — Pydantic response models and the generic `Page[T]`
  wrapper.
- `api/routers/{arxiv,factbook,openalex,gutenberg}.py` — one router per source.
  SQL is inline and the routers are intentionally thin.
- `rag/` — shared RAG primitives used by both the API and the indexer
  scripts: `chunker.py`, `embedder.py` (Ollama HTTP), `retriever.py` (RRF
  over FTS5 + sqlite-vec), `schema.py` (uniform chunks/chunks_fts/chunks_vec/
  docs_meta/_meta DDL).

## Conventions

- The API is strictly read-only. Schema and index changes belong in the
  downloader / indexer scripts under `scripts/`, not here — read-only
  connections cannot run `CREATE INDEX`. If a new filter needs an index, add
  `CREATE INDEX IF NOT EXISTS` to the relevant script and re-run it (or apply
  it once by hand against the existing DB file).
- Connections are shared across threads. This is safe because every
  connection is opened in read-only mode; do not change that without
  revisiting the threading model.
- New list filters should follow the existing pattern: build `clauses` and
  `params` lists, join with `AND`, and reuse the `Page[T]` wrapper.
