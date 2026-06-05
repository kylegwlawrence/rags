# Datasources

Status of each datasource as of 2026-06-04.

**FTS indexed** = a full-text search index exists in the primary DB so the API `/list` endpoint supports `?q=`.
**Embedded** = a `*_rag.db` exists with chunk embeddings for the `/chunks` hybrid-search endpoint.

Counts are measured live from the on-disk DBs.

---

## Active sources (data downloaded)

| Source | Fully downloaded? | On-disk size | Est. full download | FTS indexed? | Embedded? |
|--------|-------------------|-------------|-------------------|--------------|-----------|
| **arxiv** | Metadata: ✓ (1,599,403 papers, submitted 1988-11 through 2026-06-01)<br>HTML bodies: 83,192 downloaded + 16,656 no-HTML; 1.5M not yet fetched | ~78 GB (monolith at `/datasets/arxiv/arxiv.db`) | Metadata: done<br>All HTML bodies: 100s of GB | ✗ — `papers_fts` not yet rebuilt on the monolith after the sharding→monolith migration | Partial — 29,937 / 1,599,403 papers (1.9%), 49,767 chunks |
| **billstatus** | ✓ (169,862 bills, 2003 through 2026-05) | 362 MB | ~362 MB (complete) | ✓ | ✗ (summaries are short; no RAG by design) |
| **ceps / eurlex** | ✓ (142,036 EU laws, 1952–2019, from Harvard Dataverse) | 1.6 GB | ~4 GB incl. raw CSV | ✗ | Partial — 6 / 142,036 laws (on-demand only) |
| **ecfr** | ✓ (50 CFR titles, 227,600 sections) | 867 MB | ~867 MB (complete) | ✓ | Partial — 4 sections (on-demand only; full ~509k chunks ≈ 8 days) |
| **factbook** | ✓ (261 / 261 countries) | 16 MB | ~8 MB (complete) | ✗ (list/filter only; no text-search endpoint) | Partial — 24 / 261 countries (897 chunks) |
| **federal_register** | ✓ (329,851 documents, 1994-01-03 through 2026-05-21) | 446 MB | ~446 MB (complete) | ✗ — `documents_fts` not currently built | Partial — 4 documents (on-demand only) |
| **github** | ✓ for target (15 awesome-lists; 10,301 fetched, 240 missing) | 188 MB | ~188 MB (complete for target) | ✓ | Partial — 146 / 10,301 repos (2,558 chunks) |
| **gutenberg** | Metadata: ✓ (50,561 texts indexed). Bodies archived to `.tar.zst` (raw `.txt` corpus removed) | 2.3 GB (DB + RAG + archives) | ~61 GB raw mirror (archived) | ✗ (title/author/language filter only) | Partial — 983 / 50,561 texts (70,233 chunks) |
| **loc** | Partial — 4,268 manuscript / mixed-material items; 6 item PDFs fetched into the local `pdfs` DB | 15 MB | Est. hundreds of MB | ✗ (manuscript DB); ✓ on the loc_pdfs page-FTS | ✗ |
| **openalex** | ✓ for target subset (269,049 works, top-cited) | 1.0 GB | Full dataset (250M+ works): terabytes | ✓ | Partial — 4,118 / 5,000 target (82%; top-5k by citations), 6,469 chunks |
| **openstax** | ✓ (70 books, 10 subjects, 9,449 sections) | 850 MB | ~850 MB (complete) | ✓ | ✓ — 9,449 / 9,449 sections (140,582 chunks) |
| **pdfs** | ✓ for current drop folder (2 documents) | 6 MB | bounded drop folder | ✓ (page-level) | Partial — 1 / 2 documents |
| **pydocs** | ✓ (513 pages, full Python 3.13 docs) | 34 MB | ~19 MB (complete) | ✓ | Partial — 14 / 513 pages (1,223 chunks) |
| **simplewiki** | ✓ (281,168 main articles) | 4.2 GB (DB + RAG) | ~1.3 GB metadata | ✓ | ✓ — full corpus embedded (281,595 docs, 817,976 chunks) |
| **uscode** | ✓ (54 active titles, 63,137 sections) | 451 MB | ~451 MB (complete) | ✗ | ✗ |
| **worldbank** | ✓ (5,575 indicators, 303 countries/aggregates, 1,133,284 observations, 1960–2025) | 145 MB | ~145 MB (complete) | ✗ (list/filter only) | ✗ (numeric series; no RAG by design) |

---

## Compressed / offline (data exists but DB is `.zst`-archived, not currently served)

| Source | State | On-disk size | Notes |
|--------|-------|-------------|-------|
| **geonames** | `geonames.db.zst` | 923 MB (compressed) | ~13M places; decompress to serve. Lookup CSVs (`feature_classes`, `feature_codes`) still present. |
| **sec_edgar** | `sec_edgar.db.zst` | 637 MB (compressed) | ~2.9M filing metadata rows (1993–2026). `sec_edgar_rag.db` (7 docs, 1,390 chunks) still present. Decompress to serve. |
| **enwiki** | local 195 GB copy unreadable; served remotely from `raspberrypi6` over Tailscale | 195 GB (local, stale) | API proxies the pi; `enwiki_rag.db` holds 9 on-demand-embedded articles (777 chunks). |

---

## Started but empty / raw-only (script run, no served DB yet)

| Source | State | Notes |
|--------|-------|-------|
| **fred** | `fred.db` schema only (0 series, 0 observations) | FRED economic series; download not yet run to completion. |
| **noaa** | raw `stations.csv` + `years/` only, no DB | NOAA climate/weather; ingest not yet built into a DB. |
| **scotus** | empty `raw/` | SCOTUS opinions; nothing downloaded. |
| **taxcourt** | `taxcourt.db` schema only (0 opinions) | US Tax Court opinions. |
| **untreaties** | `un_treaties.db` schema only (0 treaties) | UN treaty collection. |

---

## Not yet started (script exists, no data)

| Source | Script | Notes |
|--------|--------|-------|
| **stackexchange** | `scripts/stackexchange/stackexchange_download.py` | Stack Exchange Q&A dumps. |
| **openfoodfacts** | `scripts/openfoodfacts/` | Open Food Facts product export (previously downloaded ~14 GB; data since removed). |
| **uspto** | `scripts/uspto/` | USPTO patent summaries (previously downloaded ~63 GB; data since removed). |
