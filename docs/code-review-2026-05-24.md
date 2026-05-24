# Code review — 2026-05-24

Read everything under `api/`, `rag/`, the per-source script wrappers, `requirements.txt`, and the full `frontend/`. Grouped by severity. Each item has a file:line reference so you can jump straight in.

---

## A. Real bugs

### A1. Wikihow chunk → doc navigation is broken
`api/routers/wikihow.py:113-119` types the detail route as `article_id: int`, but the RAG indexer stores `doc_id=title` (`scripts/wikihow/wikihow_rag_extract.py:130`). Clicking a chunk in semantic search calls `openDocById(chunk.doc_id)` → `/wikihow/articles/{title}` → FastAPI 422 (cannot parse title as int). Same for the per-doc Chunks tab if it were ever opened with a title-keyed doc.

Fix: either give wikihow a `/articles/by-title/{title}` route used by chunk navigation, or change the indexer to key chunks by step id (loses the "whole guide" grouping). The former is better.

### A2. OpenAlex per-doc chunks always return `[]`
`frontend/sources.js:57` sets `docIdField: 'openalex_url'`, but the indexer stores `doc_id=short_id` (`scripts/openalex/openalex_rag_extract.py:51`). DocView passes the full `https://openalex.org/W…` URL to `/openalex/doc-chunks`; the WHERE clause never matches. So the Chunks tab on any openalex work shows "No chunks indexed."

Fix: drop `docIdField: 'openalex_url'` (idField `id` already matches).

### A3. Worldbank is mounted but invisible
The router is mounted in `api/main.py:38` and `/health` probes it (`api/main.py:72`), CLAUDE.md documents the routes, but `frontend/sources.js` has no `worldbank` entry and `SOURCE_ORDER` omits it. So users can't reach it.

Fix: either add a `worldbank` source config to `sources.js` (with `contentType: 'none'` and a small `metaFields` set), or remove the router until the UI catches up.

### A4. Hardcoded personal email as default
`api/routers/sec_edgar.py:15` and `scripts/sec_edgar/sec_edgar_fetch_bodies.py:74` both default `SEC_EMAIL` to `kylegwlawrence@gmail.com`. This is a privacy leak (anyone with code access sees it; anyone using a stranger's checkout impersonates the original author to SEC).

Fix: remove the default, fail with a clear error if the env var is unset, or fall back to a placeholder like `anonymous@example.com`.

---

## B. Performance / correctness issues that aren't yet user-visible

### B1. Detail endpoints load the full body for metadata-only reads
`_lookup` helpers always SELECT the body column, even when the caller (the bare `/{id}` route) doesn't need it:
- `api/routers/arxiv.py:48-63` selects `html_content` (multi-MB per paper).
- `api/routers/sec_edgar.py:30-41` selects `body` (10-K bodies can be 100s of KB to MB).
- `api/routers/python_docs.py:23-33` selects `content`.
- `api/routers/wikihow.py:24-34` selects `text`.
- `api/routers/github_readmes.py:23-32` selects `readme`.
- `api/routers/simplewiki.py:40-49` selects `text_content` (even though `_resolve_redirect` only inspects the first 300 chars).

Fix: split `_lookup_meta` vs `_lookup_with_body`, or add a `with_body: bool` flag. For simplewiki specifically, the detail endpoint should pull `substr(text_content,1,300) AS head` and only the content endpoint should load the full body.

### B2. Worldbank `/indicators` is N+1
`api/routers/worldbank.py:34-42` calls `_topics_for(conn, indicator_id)` once per row. At limit=200 that's 200 extra queries per page.

Fix: one batch `SELECT it.indicator_id, t.name … WHERE it.indicator_id IN (?, ?, …)` and group in Python (same pattern as `_fetch_authors_many` in arxiv).

### B3. OpenAlex returns denormalized authors in the response model
`api/routers/openalex.py:33-44` splits a comma-joined `works.authors` string instead of joining the normalized `work_authors` / `authors` tables that the `?author=` filter already uses. Two sources of truth — if the normalizer adds a name or fixes a typo, the list response still shows the stale denormalized form.

Fix: use `_fetch_authors_many` style batch lookup like arxiv does.

### B4. simplewiki `_resolve_redirect` reads the full body before truncating
`_lookup` returns the full `text_content`, then the detail route calls `_resolve_redirect(conn, row["text_content"] or "", page_id)`. The walk only needs the redirect line (handled by `_find_by_title` which selects `substr(…,1,300)`), but the starting article's body comes in whole. Combine with B1.

### B5. Two slightly different redirect-aware fields on a row
`api/models.py:55-57` defines `Article.redirect_to: int | None`. Only the detail endpoint sets it. That's fine, but `_row_to_article` doesn't set it (leaves it at the dataclass default `None`), so it relies on Pydantic's default-fill. Make this explicit — easier to read.

---

## C. Patterns and structure

### C1. `index_fts.py` scripts are near-clones
`scripts/arxiv/arxiv_index_fts.py`, `…/openalex/openalex_index_fts.py`, `…/sec_edgar/sec_edgar_index_fts.py`, `…/github_readmes/…`, `…/wikihow/…`, `…/python_docs/…`, `…/federal_register/…` are copies with table/column names swapped. There's already a shared `rag.indexer.run_indexer` for the RAG side — the same idea would shrink each FTS script to ~10 lines. The only meaningful variation today is the WHERE clause for fetched-only rows (sec_edgar, github_readmes).

Fix: add `rag.fts.run_fts_indexer(db_path, virtual_table, content_table, columns, where=None)`.

### C2. Per-source RAG index-wrapper scripts repeat ~40 lines each
`arxiv_index_rag.py`, `openalex_index_rag.py`, `factbook_index_rag.py`, `gutenberg_index_rag.py`, `simplewiki_index_rag.py`, `python_docs_index_rag.py`, `wikihow_index_rag.py`, `federal_register_index_rag.py`, `sec_edgar_index_rag.py`, `github_readmes_index_rag.py` are all argparse boilerplate + the same validations + `run_indexer(...)`. Only chunk-size defaults and the extractor name vary.

Fix: factor a `rag.cli.build_index_cli(default_chunk_size, default_overlap, …)` helper that returns parsed args + calls `run_indexer`. Each script becomes ~10 lines.

### C3. `simplewiki_index_rag.py` defaults are duplicated in the API
`api/routers/simplewiki.py:23-25` hardcodes `_CHUNK_SIZE=800`, `_MAX_CHUNK_SIZE=1000`, `_OVERLAP=100` to match the batch indexer's argparse defaults. The CLAUDE.md note even calls this out as a manual sync. Once C2 exists, the indexer-default tuple can move into a shared `rag.profiles.simplewiki` (or similar) and both sides import it.

### C4. `embed_one._delete_doc` ↔ `indexer.flush()` duplicate the same delete-cascade
The "clear chunks_vec, chunks_fts, chunks, docs_meta for a doc_id" dance lives in two places (`rag/embed_one.py:21-48` and `rag/indexer.py:163-173`) and they differ slightly — `embed_one` syncs FTS incrementally with the `'delete'` command, `indexer` relies on the end-of-run rebuild. Both work, but the surface for "I forgot to clear chunks_vec" bugs is doubled.

Fix: pull `delete_doc_chunks(conn, doc_id, *, sync_fts: bool)` into `rag/schema.py` or a new `rag/_storage.py` and have both call it.

### C5. `rag/` mixes generic primitives with source-specific renderers
`rag/__init__.py:1-2` says "Shared RAG primitives." But `rag/wikitext.py`, `rag/render.py` (arxiv-specific LaTeXML), and `rag/sec_filing.py` are source-specific extraction code that just happens to be shared between a script and the API. The pattern is fine, but the location is misleading. Two options:
- Leave them under `rag/` and broaden the package docstring to "shared RAG plus any extractor reused by the API."
- Move them to `extractors/` (or under each `scripts/<source>/`, importable via the same `sys.path.insert(REPO_ROOT)` dance the indexer scripts already use).

Lean toward (a) — keeping them under `rag/` is fine, just fix the docstring.

### C6. `_fts.translate_fts_errors` is reused for non-FTS tables
`api/routers/arxiv.py:211-213` wraps the `paper_authors`/`authors` join with `translate_fts_errors`. It happens to work because the helper only special-cases "no such table" / "unable to open", but the name implies the wrapped code uses FTS5. Bad-query 400 fallback would be wrong here (a malformed authors query is your bug, not the user's).

Fix: rename the helper to `translate_table_errors` (the name it deserves) or give it a `sql_error_is_user_input: bool` flag, default True for FTS sites and False for join sites.

### C7. Mount order vs. tag order is hand-maintained twice
`api/main.py:27-38` and `frontend/sources.js:372-376` (`SOURCE_ORDER`) and the `/health` opener tuple are three separate, manually-kept lists. Adding a new source means touching all three (plus `db.py` and `routers/__init__.py`). Not a bug, but error-prone.

Fix: a small registry module that the router list and `/health` both iterate.

---

## D. Dead code / unused

### D1. `scripts/` has multiple un-routed sources
These have downloader scripts but no API router and no CLAUDE.md entry, so they're WIP or abandoned:
`billstatus/`, `ceps/`, `ecfr/`, `fred/`, `geonames/`, `noaa/`, `openfoodfacts/` (has the full RAG+FTS pipeline scripts, no router!), `scotus/`, `stackexchange/`, `taxcourt/`, `untreaties/`, `uscodes/`, `uspto/`.

Fix: either land the router or move them into `downloads_tmp/`. `openfoodfacts/` in particular has four scripts done but no router — that's the most "almost there" of the bunch.

### D2. `downloads_tmp/` is a scratch graveyard
30 scripts, gitignored via `.gitignore`. Several have names that match `scripts/<source>/` (factbook, openalex, federal_register, github_readmes, python_docs, sec_edgar, wikihow, gutenberg, loc_*) so they're the superseded drafts. It's local-only, so no harm done, but in onboarding it's noisy. Worth a one-line `downloads_tmp/README.md` saying "scratch sketches; the canonical scripts live in `scripts/<source>/`."

### D3. `rag/retry.py` has only two callers, but the parameter `exc` claims library-agnostic use
`rag/embedder.py:59,79` passes `httpx.HTTPError`. The docstring mentions `scripts/openalex_download.py` as a `requests` caller, but it doesn't seem to be imported there. If true, drop the parameter and the doc mention, or actually use it in openalex_download.

---

## E. Minor: naming and small quirks

- `api/db.py:133-153` has all the module-level `_…: sqlite3.Connection | None = None` declarations bunched at the top, in an order that doesn't match the opener function order below. Cosmetic; alphabetizing both would help diff readability.
- `api/models.py:8` `Page[T]` is fine, but `ChunksResponse` deliberately isn't a `Page` (RRF doesn't paginate). That's documented well — leave alone.
- `api/routers/federal_register.py:61-64` and `api/routers/sec_edgar.py:73-76` take `sort: str | None` instead of a `Literal[...]`/typed `Sort` as arxiv/openalex do. Drop in a Literal so FastAPI rejects unknown sort values with 422 at the boundary rather than silently falling through to the default order.
- `requirements.txt` doesn't pin `pymarc`, but CLAUDE.md says it's required for `loc_books_marc.py`. The script has a friendly "pip install pymarc" error message, so it's not blocking, but the declared deps list is incomplete. Same with `pydantic` (transitive via fastapi, but used directly in `api/models.py`).
- `frontend/sources.js` mixes `snake_case` (`subtitle_fn`, `meta_fn`) with `camelCase` (`titleField`, `idField`, `chunksEndpoint`) keys in the same object. Cosmetic.
- `frontend/components/DocView.js:218-226` and `ChunksView.js:46-50` reassign `expandedChunks.value = new Set(...)` to force reactivity after `Set.add/delete`. Vue 3 doesn't reactively track `Set` mutations; the pattern works but is mildly hacky. Could use a `Map<ref<bool>>` or `ref<Record<string, boolean>>`.
- `api/routers/openalex.py:47-65` puts `/works/{short_id}` BEFORE `/works`, which is fine because they don't conflict, but it's inconsistent with the other routers (which put the list endpoint before the detail). Cosmetic.

---

## F. Things checked that look healthy

- `api/db.py` `_connect_ro` / `_connect_ro_with_vec` / `connect_rw` separation is clean and the 503 translation is consistent.
- `api/_chunks.py` factory is a clean dedup of `/chunks` and `/doc-chunks`.
- `api/_fts.py` is small and right (modulo the name issue in C6).
- `rag/chunker.py`'s hard-cap pass + boundary preference is well-reasoned and documented.
- `rag/retriever.py` RRF + sparse-fallback path is clean; `is_operational_error` is the right hook.
- `rag/indexer.py`'s legacy-table / model-mismatch auto-rebuild is a nice touch.
- `gutenberg`'s path-escape guard (`api/routers/gutenberg.py:91-99`) is correct defense in depth.
- `simplewiki._resolve_redirect`'s cycle/hop cap is the right protection for dirty dumps.
- Frontend's hash routing + `popstate` handling is solid (back-button works on the deep paths).
- DocView's factbook `fbClean → fbEscape` order makes XSS via factbook source content very unlikely.

---

## Recommended order to tackle

1. **A1–A4** (bugs) — small, isolated, immediate user-visible wins.
2. **B1, B2** (perf) — also small, no behavior change.
3. **C1, C2, C4** (script + rag dedup) — biggest payoff for "easier to add a new source later."
4. **D1** (decide on the WIP sources) — pure project hygiene; clears noise out of the tree.
5. **C5–C7, B3–B5, E.*** — polish.
