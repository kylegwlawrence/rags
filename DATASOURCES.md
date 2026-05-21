# Datasources

Status of each datasource as of 2026-05-22.

**FTS indexed** = a full-text search index exists in the primary DB so the API `/list` endpoint supports `?q=`.
**Embedded** = a `*_rag.db` exists with chunk embeddings for the `/chunks` hybrid-search endpoint.

---

## Active sources (data downloaded)

| Source | Fully downloaded? | On-disk size | Est. full download | FTS indexed? | Embedded? |
|--------|-------------------|-------------|-------------------|--------------|-----------|
| **arxiv** | Metadata: ✓ (760k papers, harvested through 2026-05-20)<br>HTML bodies: ✗ (8,722 / 760,377 = 1%) | 8.5 GB | Metadata: ~6 GB<br>All HTML bodies: est. 100s GB | ✓ | Partial — 11,201 / 760,377 papers (1.5%); covers HTML-downloaded papers + title/abstract fallback |
| **openalex** | ✓ for target subset (268,153 works, top-cited) | 925 MB | Full dataset (250M+ works): terabytes | ✓ | Partial — 4,118 / 5,000 target (82%); target is top-5k by citation count |
| **factbook** | ✓ (261 / 261 countries) | 55 MB | ~8 MB (complete) | ✗ (list/filter only; no text search endpoint) | ✓ (261 / 261 countries) |
| **gutenberg** | ✓ (full rsync mirror, 50,521 texts indexed) | 61 GB | ~61 GB (complete) | ✗ (title/author/language filter only) | ✗ — 5 / 50,521 books (0.01%) |
| **simplewiki** | ✓ (full dump, 394,559 main articles) | 1.3 GB | ~1.3 GB (complete) | ✓ | Partial — 2,810 / 394,559 articles (0.7%) |
| **pydocs** | ✓ (513 pages, full Python 3.13 docs) | 34 MB | ~19 MB (complete) | ✓ | Partial — 14 / 513 pages (2.7%) |
| **wikihow** | ✓ (214,613 guides / 1.58M steps from static CSV) | 3.3 GB | ~3.3 GB (CSV-based, complete) | ✓ | Partial — 262 / 214,613 guides (0.1%) |
| **billstatus** | ✓ (169,862 bills, 2003-01-07 through 2026-05-20) | 269 MB | ~269 MB (complete) | ✗ | ✗ |
| **ecfr** | ✓ (50 CFR titles, 227,600 sections) | 648 MB | ~648 MB (complete) | ✗ | ✗ |
| **uscode** | ✓ (54 active titles, 63,137 sections; release 119-90; Title 53 reserved/absent by design) | 451 MB | ~451 MB (complete) | ✗ | ✗ |
| **geonames** | ✓ (13,434,712 places, full allCountries dump) | 3.4 GB | ~3.4 GB (complete) | ✗ | ✗ |
| **github** | ✓ for target (15 awesome-lists; 10,712 fetched, 240 missing) | 130 MB | ~130 MB (complete for target) | ✗ | ✗ |
| **federal_register** | ✓ (329,851 documents, 1994-01-03 through 2026-05-21) | 464 MB | ~464 MB (complete) | ✗ | ✗ |
| **loc** | ✗ (script run but 0 records downloaded) | 24 KB | Est. hundreds of MB (495k English manuscripts) | ✗ | ✗ |
| **ceps / eurlex** | ✓ (142,036 EU laws, 1952–2019, from Harvard Dataverse) | 1.6 GB (DB); 4.0 GB total incl. raw CSV | ~4 GB (complete) | ✗ | ✗ |
| **uspto** | ✓ (6,423,626 patent summaries, 2000–2025) | 63 GB | ~63 GB (complete) | ✗ | ✗ |
| **openfoodfacts** | ✓ (4,490,000 products, full JSONL export) | 14 GB | ~14 GB (complete) | ✗ | ✗ |
| **sec_edgar** | ✓ (2,928,790 filings, 1993-08 through 2026-05) | 805 MB | ~805 MB (complete) | ✗ | ✗ |

---

## Not yet started (script exists, no data)

| Source | Script | Notes |
|--------|--------|-------|
| **lib_congress_books** | `scripts/lib_congress_books/lib_congress_books_download.py` | LOC book catalog |
| **lib_congress_manuscripts** | `scripts/lib_congress_manuscripts/lib_congress_manuscripts.py` | LOC manuscripts |
| **lib_congress_newspapers** | `scripts/lib_congress_newspapers/lib_congress_newspapers_download.py` | LOC digitized newspapers |
| **stackexchange** | `scripts/stackexchange/stackexchange_download.py` | Stack Exchange Q&A dumps |
| **chembl** | `scripts/chembl/chembl_download.py` | ChEMBL chemistry/drug compound database |
| **clinicaltrials** | `scripts/clinicaltrials/clinicaltrials_download.py` | ClinicalTrials.gov trial metadata |
| **congress_summaries** | `scripts/congress_summaries/congress_summaries_download.py` | CRS bill summaries (may overlap with billstatus) |
| **courtlistener** | `scripts/courtlistener/courtlistener_download.py` | CourtListener legal opinions |
| **dailymed** | `scripts/dailymed/dailymed_download.py` | FDA DailyMed drug label data |
| **kaggle** | `scripts/kaggle/kaggle_download.sh` | Kaggle datasets (general) |
| **noaa** | `scripts/noaa/noaa_download.py` | NOAA climate/weather data |
| **taxcourt** | `scripts/taxcourt/taxcourt_download.py` | US Tax Court opinions |
| **un_treaties** | `scripts/un_treaties/un_treaties_download.py` | UN treaty collection |
| **worldbank** | `scripts/worldbank/worldbank_data360_download.py` | World Bank Data360 indicators |
