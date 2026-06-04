# datasets

A personal collection of one-shot downloader scripts that fetch public
datasets into local SQLite files under `data/<source>/`, plus a read-only
FastAPI app (`api/`) that exposes them over the local (Tailscale) network.
Each downloader is independent — no shared build system; the API just reads
whatever DB files happen to be present.

This README focuses on the API and the overall shape. For per-script details
(download cadence, indexer order, flags, known limitations) see `CLAUDE.md`.

## Sources

The API currently serves these sources (each is a router under `api/routers/`,
backed by one or more SQLite files in `data/<source>/`):

| Source | What it is | Free-text (`q`) | Semantic (`/chunks`) |
|--------|------------|:---:|:---:|
| **arxiv** | arXiv paper metadata + HTML bodies | ✓ | ✓ |
| **factbook** | CIA World Factbook country profiles | — | ✓ |
| **openalex** | OpenAlex scholarly works | ✓ | ✓ |
| **gutenberg** | Project Gutenberg texts | — | ✓ |
| **simplewiki** | Simple English Wikipedia articles | ✓ (title) | ✓ |
| **enwiki** | Full English Wikipedia (remote, on a Raspberry Pi) | ✓ (title) | — |
| **pydocs** | Python standard-library documentation | ✓ | ✓ |
| **federal_register** | US Federal Register documents (1994–present) | ✓ | ✓ |
| **github** | Repository READMEs from 15 "awesome" lists | ✓ | ✓ |
| **sec_edgar** | SEC EDGAR filing metadata + fetched bodies | ✓ | ✓ |
| **worldbank** | World Bank indicators + observations | ✓ | — |
| **geonames** | ~13M named geographic features | ✓ | — |
| **billstatus** | US congressional bills (108th–present) | ✓ | — |
| **eurlex** | EU laws, 1952–2019 (CEPS EurLex snapshot) | — | ✓ |
| **ecfr** | Electronic Code of Federal Regulations | ✓ | ✓ (on demand) |
| **openstax** | OpenStax textbooks (books / chapters / sections) | ✓ | ✓ |
| **pdfs** | Locally-ingested PDFs | ✓ | ✓ |

`✓` under **Free-text** means the source has an FTS5 index built by its
`*_index_fts.py` script; `✓` under **Semantic** means it has a `*_rag.db` and
a `/chunks` endpoint.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements.txt   # first time only
```

Each source's DB files must already exist (built by the scripts in
`scripts/<source>/` — see `CLAUDE.md` for the order). If a database is
missing, that source's routes return 503 but the rest of the app keeps
serving. `GET /health` runs `SELECT 1` against every connection and returns
per-DB status (HTTP 503 if any DB is broken, 200 otherwise) so a probe can
tell which one failed in a single call.

Semantic search (`/chunks`) needs a local [Ollama](https://ollama.com) running
`nomic-embed-text:v1.5` (768-dim) at `OLLAMA_URL` for the dense arm; if Ollama
is unreachable the endpoint degrades to sparse-only FTS (`used_dense=false`).

## Running

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8002
```

Port `8002` is fixed (8000 and 8001 are taken by other local uvicorn apps).
Binding to `0.0.0.0` is intentional so the Tailscale interface picks it up.
There is no app-level auth — access is gated by Tailscale ACLs. Do not expose
this port directly to the public internet. OpenAPI docs are at `/docs`.

**Reload after indexing.** Read connections are cached at module load, so
restart uvicorn after any downloader / indexer run. The exception is the
live-write routes (see below), whose committed rows the cached read-only
connection sees on its next query without a restart.

## Common response shapes

**List endpoints** (`/papers`, `/works`, `/documents`, …) take `limit`
(default 50, max 200) and `offset`, and return a `Page[T]`:

```json
{ "items": [...], "total": 1234, "limit": 50, "offset": 0 }
```

**Chunk (semantic-search) endpoints** (`/<source>/chunks`) take a required
`q`, plus `top_k` and `candidate_k`, and return:

```json
{ "items": [...], "used_dense": true, "top_k": 20, "candidate_k": 50 }
```

Results are merged with Reciprocal Rank Fusion (RRF) over an FTS5 sparse arm
and a sqlite-vec dense arm — not paginated. An empty `q` returns 400; a
missing `*_rag.db` returns 503 naming the indexer script; bad FTS5 syntax
returns 400; Ollama down → `used_dense=false` (sparse-only). Several sources
also expose `/<source>/doc-chunks?doc_id=…`, which returns every stored chunk
for one document in insertion order.

**Live-embed routes** (`POST /<source>/.../embed`) embed one document on demand
into that source's `*_rag.db` — the "Embed" button in the frontend. They now
exist on most sources (arxiv, openalex, gutenberg, simplewiki, ecfr, eurlex,
enwiki, pdfs, openstax, federal_register, github, sec_edgar). The only other
write route is `POST /sec_edgar/filings/{accession_number}/download`, which
fetches one filing body in place. Everything else is strictly read-only.

## Endpoints by source

Only the distinctive filters are listed below; every list endpoint also takes
`limit` / `offset`, and every `/chunks` endpoint takes `q` / `top_k` /
`candidate_k`. See `/docs` for the full schema.

### arXiv `/arxiv`
A single monolithic DB at `/datasets/arxiv/arxiv.db` (kept outside the repo — the
~80 GB file is too big for `/home`), opened read-only via `api/db.py` `arxiv()`.

- `GET /papers` — `?primary_category=` (exact), `?category=` (substring),
  `?submitted_year=`, `?submitted_from=`/`?submitted_to=` (ISO range),
  `?author=` (substring), `?has_html=`, `?q=` (FTS over title + abstract),
  `?sort=` (`submitted_desc|submitted_asc|updated_desc|relevance`). With `q`,
  `sort=relevance` ranks by `bm25(papers_fts)`; date sorts are exact.
- `GET /papers/{paper_id}` / `/{paper_id}/content` (raw HTML body) /
  `POST /{paper_id}/embed`. Old-style ids with slashes (e.g. `cond-mat/0204015`)
  are supported via a `:path` route.
- `GET /arxiv/chunks` — the global `arxiv_rag.db`.

### Factbook `/factbook`
- `GET /countries` — `?region=` (exact); `GET /countries/{id}`.
- `GET /factbook/chunks` — chunks tagged with their factbook section
  (Geography, Economy, …).

### OpenAlex `/openalex`
- `GET /works` — `?q=`, `?year=`, `?cited_by_min=`/`?cited_by_max=`,
  `?venue=`, `?domain=` / `?field=` (exact, on the work's primary-topic
  hierarchy), `?author=` (joins the normalized author tables), `?embedded=`,
  `?sort=` (`cited_by_count_desc|year_desc|year_asc|relevance`).
- `GET /works/{short_id}` (the `W…` suffix) / `POST /{short_id}/embed`.
- `GET /openalex/chunks` — top-5000 most-cited works.

### Gutenberg `/gutenberg`
- `GET /texts` — `?title=` / `?author=` (substring), `?language=` (exact).
- `GET /texts/{id}` / `/{id}/content` (streams the raw `.txt`) /
  `POST /{id}/embed`.
- `GET /gutenberg/chunks`.

### Simple Wikipedia `/simplewiki`
- `GET /articles` — `?q=` (title FTS); `GET /{page_id}` / `/{page_id}/content` /
  `POST /{page_id}/embed`.
- `GET /simplewiki/chunks`.

### English Wikipedia `/enwiki` (remote proxy)
The 76 GB DB lives on `raspberrypi6`; a tiny FastAPI service serves it over
Tailscale and this app proxies. Read-only, title-only FTS, no `/chunks` in v1.
Returns 503 when `ENWIKI_REMOTE_URL` is unset or the Pi is unreachable.
- `GET /articles` — `?q=` (title FTS), `?title=` (substring), `?namespace=`;
  `GET /{page_id}` / `/{page_id}/content`.

### Python docs `/pydocs`
- `GET /docs` — `?q=`; `GET /{doc_path:path}` / `/{doc_path:path}/content`.
- `GET /pydocs/chunks`.

### Federal Register `/federal_register`
- `GET /documents` — `?q=` (FTS over title + abstract), `?type=` (exact:
  `Rule`, `Proposed Rule`, `Notice`), `?agencies=` (substring),
  `?publication_year=`, `?embedded=`, `?sort=` (`newest|oldest|relevance`).
- `GET /{document_number}` / `/{document_number}/content` (abstract → excerpts) /
  `POST /{document_number}/embed`.
- `GET /federal_register/chunks` + `/doc-chunks`.

### GitHub READMEs `/github`
READMEs harvested from 15 curated "awesome" lists; only `fetched` rows are served.
- `GET /readmes` — `?q=` (FTS over repo name + body), `?owner=` (substring),
  `?source_list=` (the awesome-list it came from), `?embedded=`.
- `GET /{repo:path}` / `/{repo:path}/content` (raw markdown) /
  `POST /{repo:path}/embed`. `repo` is the `owner/name` slug.
- `GET /github/chunks` + `/doc-chunks`.

### SEC EDGAR `/sec_edgar`
Surfaces metadata-only filings whose body hasn't been downloaded yet.
- `GET /filings` — `?q=` (company + body), `?downloaded=` (narrow to
  fetched/unfetched).
- `GET /{accession_number}` / `/{accession_number}/content` /
  `POST /{accession_number}/download` (fetch one body in place) /
  `POST /{accession_number}/embed`.
- `GET /sec_edgar/chunks`.

### World Bank `/worldbank`
- `GET /indicators` — `?q=`, `?topic=`; `GET /indicators/{id}` /
  `/indicators/{id}/values` (`?country=`, `?year=`).
- `GET /countries` / `/countries/{id}/data` (`?topic=`, `?year=`). No `/chunks`.

### GeoNames `/geonames`
~13M places; **no RAG** (rows are one-line records). The places table is huge —
always pass at least one filter.
- `GET /places` — `?q=` (FTS over name + country + description),
  `?country_code=` (ISO-2), `?feature_class=` / `?feature_code=` (both
  repeatable), `?min_population=`. Default sort is population-descending.
- `GET /places/{geonameid}`.
- `GET /feature_classes` / `GET /feature_codes` (`?feature_class=` repeatable) —
  lookups that populate the frontend's multi-select dropdowns.

### BillStatus `/billstatus`
- `GET /bills` — `?q=` (title + summary + subjects), `?congress=`,
  `?bill_type=`, `?sponsor=`, `?policy_area=`, `?subject=`, `?sort=`.
- `GET /{bill_id}` / `/{bill_id}/content`. `bill_id` is `{congress}-{TYPE}-{number}`
  (e.g. `118-HR-1234`). No RAG.

### EUR-Lex `/eurlex`
- `GET /laws` — `?q=`, `?act_type=`, `?status=`, `?author=`, `?embedded=`;
  `GET /{celex}` / `/{celex}/content` / `POST /{celex}/embed`.
- `GET /eurlex/chunks` (flat-prose chunks over the law bodies).

### eCFR `/ecfr`
One row per CFR section. RAG is **on-demand only** (no batch indexer — the full
corpus is ~509k chunks ≈ 8 days on local Ollama).
- `GET /regulations` — `?q=` (heading + content), `?title=`, `?part=`,
  `?embedded=`, `?sort=` (`relevance` requires `q`, else reading order).
- `GET /{reg_id}` / `/{reg_id}/content` / `POST /{reg_id}/embed`.
- `GET /ecfr/chunks`.

### OpenStax `/openstax`
Textbooks as `books` / `chapters` / `sections`. The browsable unit is the
**section**; `/content` is light Markdown with inline/display LaTeX (rendered
with KaTeX in the frontend).
- `GET /books` — `?q=`, `?subject=`; `GET /{book_id}`.
- `GET /sections` — `?q=` (title + objectives + body), `?book_id=`,
  `?subject=`, `?embedded=`, `?sort=`; `GET /sections/{section_id:path}` /
  `/content` / `POST /embed`. A section's id is `{book_id}/{module_id}`.
- `GET /openstax/chunks` — accepts metadata filters that scope retrieval:
  `?subject=` and `?book_id=` are **repeatable** (OR within a list),
  `?chapter_number=` is single-value. Each hit carries
  `book_id` / `subject` / `chapter_number` / `chapter_title` provenance.
  Plus `/openstax/doc-chunks`.

### PDFs `/pdfs`
One row per ingested PDF; chunked **page by page** so each chunk's `section` is
its page label (`"p. 42"`), which deep-links the viewer (`#page=N`).
- `GET /documents` — `?q=` (page text, rolled up to documents), `?title=`,
  `?author=`, `?sort=` (`relevance|recent`).
- `GET /{doc_id}` / `/{doc_id}/content` (streams the original PDF) /
  `POST /{doc_id}/embed`.
- `GET /pdfs/chunks` + `/doc-chunks`.

## Layout

- `api/main.py` — mounts every router and `/health`.
- `api/db.py` — read-only module-level SQLite connections; `connect_rag_rw`
  for live-embed writes; `connect_rw` for the SEC live body-download write.
- `api/models.py` — `Page[T]` for list endpoints, `ChunksResponse` for RAG,
  `EmbedResult` for the embed routes.
- `api/routers/` — one thin router per source; SQL is inline.
- `api/_chunks.py` — shared `/chunks` + `/doc-chunks` factory.
- `api/_fts.py` — translates FTS errors (missing table → 503, bad syntax → 400).
- `rag/` — shared RAG primitives used by both the API and the indexer scripts:
  `chunker.py` (`chunk_doc` / `chunk_markdown`), `cleaner.py`
  (`CLEANER_VERSION`), `embedder.py` (Ollama HTTP), `retriever.py` (RRF),
  `schema.py`, plus per-source `build_doc` modules (`eurlex.py`, `pdfs.py`,
  `openstax.py`, `federal_register.py`, `sec_filing.py`, …).
- `tests/` — pytest smoke suite; run with `pytest`.

## Conventions

- The API is strictly read-only. Schema and index changes belong in the
  downloader / indexer scripts under `scripts/`, not here — read-only
  connections cannot run `CREATE INDEX`. When a new filter needs an index, add
  `CREATE INDEX IF NOT EXISTS` to the relevant script and re-run it.
- Each new source: a script in `scripts/<source>/`, data in `data/<source>/`,
  a thin router in `api/routers/`. Use `INSERT OR REPLACE` / `INSERT OR IGNORE`
  for idempotent re-runs.
- New list filters follow the existing pattern: build `clauses` / `params`
  lists, join with `AND`, reuse the `Page[T]` wrapper.
</content>
</invoke>
