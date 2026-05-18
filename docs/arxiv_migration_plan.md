# Plan: Separate the arXiv Backend (Phase 1 — Read-Only Metadata API)

## Context
Refer to `/Users/kylelawrence/Documents/PROJECTS/local_wikipedia/arxiv` for the existing arXiv backend code (note that this is coupled with a local wikipedia backend and a frontend search/embedding application).

The arXiv module in this repo (`arxiv/`) is tightly coupled to the local FastAPI frontend through templates, HTMX routes, and shared infrastructure (`dumps/jobs.db`, `paths.py`, `db.py`, `workers/runner.py`). The user wants to:

1. Access arXiv data from other applications.
2. Run the data on a remote LAN machine.
3. Co-locate arXiv with three existing dataset APIs at `/Users/kylelawrence/Documents/PROJECTS/rags`.
4. Follow that repo's established API pattern.
5. Reserve the option to switch frontends.
6. Reduce repo size for easier Claude-assisted development.

The work is staged across phases. **This plan covers Phase 1 only**: a read-only metadata API at the `rags` repo, modeled on its existing OpenAlex router. Chunking, embedding, semantic search, and ingest automation are deferred.

## Phase 1 Scope

**In scope**

- New read-only router `/arxiv/papers` in the `rags` repo.
- Paper metadata exposed via list + detail endpoints (id, title, abstract, authors, categories, dates, etc.).
- FTS5 search over `title + abstract` with `bm25`-relevance sort.
- Server-side filtering (category, year, date range, author substring, has-html).
- `/arxiv/papers/{id}/content` returning raw downloaded HTML body (mirrors the Gutenberg `/content` pattern).
- One-shot indexer script `scripts/arxiv_index_fts.py` to build the FTS5 index.
- Standard pagination + error envelope matching every other router in `rags`.

**Out of scope**

- `paper_chunks` / `paper_chunks_fts` / `paper_chunks_vec` tables (Phase 2).
- Semantic search via Ollama embeddings (Phase 2).
- Ingest / download / embed automation on the remote (Phase 2+).
- Wiki RAG separation (later phase).
- Author normalization tables — that's an OpenAlex-specific pattern; arxiv uses substring matching against the JSON `papers.authors` text.
- Any change to the local `arxiv/` package in this repo. The wiki frontend stays exactly as is.
- A `tests/` directory in the `rags` repo. The retro flags a minimal `pytest` smoke suite as a positive-ROI carry-over, but it's a new convention; introducing it alongside arxiv would scope-creep this phase. Tracked as a follow-up below.

## Known limitations inherited from upstream

- **`papers.authors` is a JSON array of `"forenames keyname"` strings** (`json.dumps` at [`arxiv/ingest.py:55`](../../arxiv/ingest.py), built from `<keyname>` + `<forenames>` in [`arxiv/oai.py:258`](../../arxiv/oai.py)). Per-author boundaries are preserved — strictly better than OpenAlex's comma-joined string — so there's no "Smith, Jr." fragmentation risk. But two things from the OAI feed are dropped at parse time: (a) the surname/forenames split is collapsed into a single concatenated string, and (b) any `<affiliation>` / other `<author>` sub-elements aren't captured. Phase 1 lives with this; per [`rags/RULES.md`](../../../../rags/RULES.md) §6 ("preserve foreign-system identity columns at ingest"), Phase 3 should restore the structural detail when ingest moves to `rags`. Calling it out now so the lesson lands when ingest is ported.

## Architecture

```
This laptop (local_wikipedia repo)              Remote LAN machine (rags repo)
─────────────────────────────────────            ────────────────────────────────
arxiv/ingest  (manual `python -m`)               api/main.py  (FastAPI, port 8002)
   └→ dumps/arxiv.db  (source of truth)              └→ data/arxiv/arxiv.db
            │                                              ↑
            └────────── periodic rsync ───────────────────┘
                                                    (re-run scripts/arxiv_index_fts.py after each sync)
```

Two clean boundaries:

- Data: a single SQLite file copied over the network. No remoting protocol, no shared schema migrations.
- HTTP: read-only API over Tailscale ACLs (no app-level auth — matches the existing `rags` convention).

## Changes in `/Users/kylelawrence/Documents/PROJECTS/rags`

All paths in this section are relative to that repo's root.

### 1. `data/arxiv/arxiv.db` — new (copied from this repo)

Bootstrap data: copy `dumps/arxiv.db` from `local_wikipedia` to `rags/data/arxiv/arxiv.db`. The schema already has every column the API will read (see [`arxiv/schema.py:50`](../../arxiv/schema.py) — `papers` table with title, abstract, authors, categories, primary_category, submitted_date, updated_date, doi, journal_ref, comments, html_content, download_status, downloaded_at). No schema migration is required on the source side.

### 2. `scripts/arxiv_index_fts.py` — new

Mirror `scripts/openalex_index_fts.py` exactly:

```python
# pseudocode — actual script follows the openalex_index_fts.py shape verbatim
conn.executescript("""
    DROP TABLE IF EXISTS papers_fts;
    CREATE VIRTUAL TABLE papers_fts USING fts5(
        title, abstract,
        content='papers',
        content_rowid='rowid',
        tokenize='porter unicode61'
    );
""")
conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
```

- External-content table: index lives in `papers_fts`, original text stays in `papers` — no duplication.
- `porter unicode61` matches OpenAlex's tokenizer choice (handles accented characters in titles/abstracts).
- Drop+rebuild on each run is the established `rags` convention.

This script must be run once after the initial DB copy, and re-run after every refresh rsync.

### 3. `api/db.py` — modified

Three additions mirroring the existing openalex/factbook/gutenberg shape:

```python
ARXIV_DB = DATA_DIR / "arxiv" / "arxiv.db"
_arxiv: sqlite3.Connection | None = None

def arxiv() -> sqlite3.Connection:
    """Cached read-only connection to arxiv.db."""
    global _arxiv
    if _arxiv is None:
        _arxiv = _connect_ro(ARXIV_DB)
    return _arxiv
```

### 4. `api/models.py` — modified

Single new Pydantic model, used for both list and detail responses (no separate `PaperDetail` — keeps shape uniform):

```python
class Paper(BaseModel):
    id: str
    title: str
    abstract: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    submitted_date: str
    updated_date: str | None
    doi: str | None
    journal_ref: str | None
    comments: str | None
    has_html: bool   # papers.download_status == "downloaded"
```

`html_content` is intentionally not on the model — it's available via `/content` only.

Reuse the existing `Page[T]` generic for list responses (no new envelope).

### 5. `api/routers/arxiv.py` — new (the main work)

Layout mirrors `api/routers/openalex.py`. `prefix="/arxiv"`, `tags=["arxiv"]`. Three endpoints:

**`GET /arxiv/papers` — list with filters + FTS + sort + pagination**

Query parameters:

| Param | Type | Notes |
|---|---|---|
| `q` | `str \| None` | FTS5 syntax over title + abstract via `papers_fts MATCH ?`. See "Error model" below for status-code mapping. |
| `primary_category` | `str \| None` | Exact match: `WHERE primary_category = ?`. Backed by `idx_papers_primary_cat`. |
| `category` | `str \| None` | Substring against `papers.categories`: `WHERE categories LIKE '%' \|\| ? \|\| '%'`. Handles multi-category papers without a normalized table. |
| `submitted_year` | `int \| None` | Year prefix: `WHERE submitted_date LIKE ? \|\| '-%'`. |
| `submitted_from`, `submitted_to` | `str \| None` | ISO date range: `WHERE submitted_date >= ?`, `<= ?`. |
| `author` | `str \| None` | Substring against `papers.authors`: `WHERE authors LIKE '%' \|\| ? \|\| '%'`. (Not normalized — see Phase 1 scope.) |
| `has_html` | `bool \| None` | `WHERE download_status = 'downloaded'` (or `!= 'downloaded'`). |
| `sort` | `Literal["submitted_desc","submitted_asc","updated_desc","relevance"]` | Default = `relevance` when `q` set, else `submitted_desc`. `relevance` requires `q` (else 400, matching openalex). |
| `limit` | `Query(50, ge=1, le=200)` | |
| `offset` | `Query(0, ge=0)` | |

Response: `Page[Paper]`.

**`GET /arxiv/papers/{paper_id:path}` — detail**

Uses `{paper_id:path}` to handle old-style ids with slashes (e.g. `cond-mat/0204015`). Returns `Paper` or `HTTPException(404, f"paper {paper_id!r} not found")`.

**`GET /arxiv/papers/{paper_id:path}/content` — raw HTML body**

`SELECT html_content FROM papers WHERE id = ?`. If row missing → 404 paper-not-found. If `html_content IS NULL` → 404 with `detail="paper has no downloaded HTML"`. Otherwise `Response(content=html_content, media_type="text/html; charset=utf-8")`.

**Helpers**

A `_row_to_paper(row)` analog of `_row_to_work`:

- `authors = json.loads(row["authors"])` — `papers.authors` is a JSON array (see "Known limitations inherited from upstream" above). Don't split on `", "` — that would shred names with embedded commas.
- `categories = row["categories"].split()` — `papers.categories` is a whitespace-separated string from the OAI feed (e.g. `"cs.CL cs.LG"`); see [`arxiv/oai.py:239`](../../arxiv/oai.py).
- `has_html = (row["download_status"] == "downloaded")`.

**Error model** (per [`rags/RULES.md`](../../../../rags/RULES.md) §5 — match HTTP severity to who failed)

- **400** for user errors: malformed FTS5 syntax in `q`, `sort=relevance` without `q`, anything FastAPI's `Query` validators reject.
- **404** for legitimate misses: paper not found, paper has no downloaded HTML.
- **503** for operational errors: `papers_fts` missing (run `scripts/arxiv_index_fts.py`), `data/arxiv/arxiv.db` missing or empty. Detect on the `sqlite3.OperationalError` message — `"no such table"` and `"unable to open database file"` both map to 503 with a `detail` that names the script to run. This is a deliberate departure from `openalex.py`, which currently returns 400 in the missing-`works_fts` case — flagged as a regret in `RAGS_RETRO.md` and not propagated to new routers.

### 6. `api/main.py` — modified

Three small edits:

- Add `arxiv` to `from api.routers import factbook, gutenberg, openalex`.
- Add `app.include_router(arxiv.router)`.
- Add `("arxiv", db.arxiv)` to the tuple iterated by `/health`.

### 7. `README.md` and `CLAUDE.md` — modified

Per [`rags/RULES.md`](../../../../rags/RULES.md) §10 ("update docs in the same change that broke them"), both docs get arxiv entries — they have different audiences and shouldn't be conflated:

- **`README.md`** — add an "ArXiv" subsection under "Endpoints" (list endpoint with params, detail, content); add `data/arxiv/arxiv.db` plus the `papers_fts` index to the "API expects these files to already exist" list; add `api/routers/arxiv.py` to the "Layout" list.
- **`CLAUDE.md`** — add `arxiv` to the routers paragraph in the running list, a per-script note for `arxiv_index_fts.py` (drop+rebuild semantics, run cadence, ~time), and the rsync refresh procedure. Don't duplicate the README's API surface here.

## Changes in this repo (`local_wikipedia`)

**None.** The local `arxiv/` package, its routes, its templates, and the wiki frontend are unmodified. The new API stands up alongside the existing local app; neither depends on the other.

Future phases may eventually retire `arxiv/` from this repo, but that's explicitly out of Phase 1 scope.

## Data Migration

One-time bootstrap, then a refresh loop. Commands run on the remote LAN machine (with SSH access to this laptop):

```bash
# 1. One-time setup
cd ~/projects/rags  # adjust to actual location on the remote
mkdir -p data/arxiv
rsync -avh laptop:/Users/kylelawrence/Documents/PROJECTS/local_wikipedia/dumps/arxiv.db data/arxiv/arxiv.db

# 2. Build the FTS5 index
source .venv/bin/activate
python scripts/arxiv_index_fts.py

# 3. Restart the API to refresh the cached connection
# (api/db.py caches `sqlite3.connect` at module level — see its docstring)
sudo systemctl restart rags-api   # or whatever the service unit is called
```

For refreshes, re-run steps 1–3.

**Why the restart matters** — `api/db.py` caches the `sqlite3.Connection` at module level (see its docstring). If a downloader rewrites the DB while uvicorn is running, the cached handle keeps pointing at the old file inode (or fails on a truncated file). The retro flagged this as a quiet footgun: nobody has been bitten yet, but the restart is mandatory after re-indexing, not optional. Per `rags/WORK.md` §1.5, run stop → confirm → start as separate commands; don't chain a `pkill && uvicorn &`.

## Verification

End-to-end smoke tests after deploying Phase 1:

1. **Server starts** — `uvicorn api.main:app --host 0.0.0.0 --port 8002` with no import errors.
2. **Health** — `curl http://<host>:8002/health` includes `"arxiv": "ok"`.
3. **List default** — `curl 'http://<host>:8002/arxiv/papers?limit=5'` returns five papers, `sort=submitted_desc` applied. Verify `items[*].has_html` reflects `download_status` on the underlying rows.
4. **FTS** — `curl 'http://<host>:8002/arxiv/papers?q=attention&limit=3'` returns matching papers. Compare bm25 ordering against known-relevant titles.
5. **Bad FTS syntax (user error)** — `curl -i 'http://<host>:8002/arxiv/papers?q=%22'` returns **400** with `detail` containing the SQLite error string.
6. **Missing FTS index (operational error)** — drop `papers_fts` against a scratch copy of the DB (or use a freshly-rsynced copy where the indexer hasn't run yet) and `curl -i 'http://<host>:8002/arxiv/papers?q=foo'` returns **503** with `detail` naming `scripts/arxiv_index_fts.py`. Distinguishes operational from syntax errors per `RULES.md` §5.
7. **sort=relevance without q** — returns `400` ("sort=relevance requires q").
8. **Detail (new-style id)** — `curl 'http://<host>:8002/arxiv/papers/2310.06825'` returns one paper.
9. **Detail (old-style id with slash)** — `curl 'http://<host>:8002/arxiv/papers/cond-mat/0204015'` (URL-encode if needed) confirms the `:path` converter handles old ids.
10. **404 paper** — `curl -i 'http://<host>:8002/arxiv/papers/9999.99999'` returns 404 with `detail`.
11. **Filters** — `?primary_category=cs.CL&submitted_year=2024` returns only matching papers; `?author=Vaswani` returns Vaswani papers.
12. **Content present** — `curl 'http://<host>:8002/arxiv/papers/<downloaded-id>/content'` returns HTML, `Content-Type: text/html`.
13. **Content absent** — `curl -i 'http://<host>:8002/arxiv/papers/<un-downloaded-id>/content'` returns 404 with `detail="paper has no downloaded HTML"`.
14. **OpenAPI docs** — `http://<host>:8002/docs` shows the three new endpoints under the `arxiv` tag.

No automated tests added with this phase — the `rags` repo doesn't have a test suite, and introducing one alongside arxiv would scope-creep. The retro identifies a minimal `pytest` smoke suite (one happy-path per route + the FTS-syntax 400 + relevance-without-q 400) as a high-ROI carry-over — recommend tackling it as a separate, repo-wide change after Phase 1 ships rather than buried here.

## Critical files to read first when executing

Code templates:

- [`/Users/kylelawrence/Documents/PROJECTS/rags/api/routers/openalex.py`](../../../../rags/api/routers/openalex.py) — the pattern template. The arxiv router is mechanically derived from this.
- [`/Users/kylelawrence/Documents/PROJECTS/rags/scripts/openalex_index_fts.py`](../../../../rags/scripts/openalex_index_fts.py) — template for `arxiv_index_fts.py`.
- [`/Users/kylelawrence/Documents/PROJECTS/rags/api/db.py`](../../../../rags/api/db.py) — connection pattern.
- [`/Users/kylelawrence/Documents/PROJECTS/rags/api/models.py`](../../../../rags/api/models.py) — `Page[T]` envelope.
- [`/Users/kylelawrence/Documents/PROJECTS/local_wikipedia/arxiv/schema.py`](../../arxiv/schema.py) — `papers` table shape; the source of truth for column names.
- [`/Users/kylelawrence/Documents/PROJECTS/local_wikipedia/arxiv/ingest.py`](../../arxiv/ingest.py) and [`oai.py`](../../arxiv/oai.py) — how the `authors` and `categories` columns are encoded (JSON array, whitespace-separated text) — drives the `_row_to_paper` helper.

Conventions (read once before coding):

- [`/Users/kylelawrence/Documents/PROJECTS/rags/RULES.md`](../../../../rags/RULES.md) — the 10 non-negotiables. Most directly relevant here: §4 (parameterize input), §5 (HTTP severity matches who failed — drives the 400/503 split above), §6 (preserve foreign-system identity columns — drives the Phase 3 author-structure note), §10 (update docs in the same change).
- [`/Users/kylelawrence/Documents/PROJECTS/local_wikipedia/docs/RAGS_RETRO.md`](../RAGS_RETRO.md) — the project retro that informed several decisions in this plan: 503 vs 400 for missing FTS, the connection-cache restart footgun, the deferred test suite, the author-structure carry-over.

## Future Phases (not in scope here)

| Phase | Scope |
|---|---|
| **Phase 2** | Port `paper_chunks` / `paper_chunks_fts` / `paper_chunks_vec` to `rags`. Add `GET /arxiv/chunks?q=` for hybrid (dense + sparse + RRF) semantic search. Ollama placement becomes a concrete decision here. |
| **Phase 3** | Port `arxiv/oai.py`, `arxiv/ingest.py`, `arxiv/download.py`, `arxiv/embed_paper.py`, and the embed worker to `rags`. **Per `RULES.md` §6, the ported `oai.py` parser should preserve structured author fields** — at minimum keep `<keyname>` and `<forenames>` separate (instead of `f"{forenames} {keyname}"`), and capture `<affiliation>` if present. This is the proper fix for the Phase 1 author-filter limitation; don't repeat the OpenAlex retro lesson here. API stays read-only; ingestion runs as cron + scripts on the remote box. `arxiv/` can be removed from this repo at this point. |
| **Phase 4** | Same separation for the wiki RAG. Reuses Phase 2/3 plumbing. |
| **Phase 5** | Decide whether the local frontend stays (consumes the new APIs), gets replaced, or is retired. |

## After Phase 1 ships

Write a short retro alongside `RAGS_RETRO.md` (same shape: Summary / What went well / What went wrong / Decisions worth remembering / Carry-over). The existing retro flagged "phases 1 and 2 don't have retros because I didn't write them at the time" as a regret — don't repeat that here while the work is fresh.
