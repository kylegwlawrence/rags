# Plan: Datasets Frontend

## Context
Personal dataset browser UI for the datasets repo API (port 8002). Needs to browse and read documents from 4 initial sources (arxiv, openalex, simplewiki, gutenberg), render content appropriately per source, and run semantic chunk search. Decoupled frontend — pure HTML/CSS/JS, no Python, no build step. Vue 3 vendored locally (no CDN).

---

## File layout

```
frontend/
├── index.html          # HTML shell — loads style.css + main.js
├── style.css           # Design system: CSS custom properties, light/dark, layout
├── main.js             # createApp entry, imports App.js
├── App.js              # Root component: activeSource, activeView, selectedDoc state
├── sources.js          # Per-source config: endpoints, filters, field mappings, content type
├── api.js              # Fetch helpers: listDocs, getDoc, getContent, getChunks
├── components/
│   ├── SourceNav.js    # Sidebar — source selector chips
│   ├── BrowseView.js   # Search bar + filters + paginated list
│   ├── DocView.js      # Metadata + content panel + "Chunks" tab
│   └── ChunksView.js   # Global semantic search across the active source
└── vendor/
    ├── vue.esm-browser.js   # Downloaded via curl from jsdelivr
    └── marked.min.js        # Downloaded via curl from jsdelivr
```

---

## Backend change (one file)

**`api/main.py`** — add after the router includes:
```python
from fastapi.staticfiles import StaticFiles
app.mount("/ui", StaticFiles(directory="frontend", html=True), name="ui")
```
No CORS needed — same origin (both at port 8002).

---

## Source config (`sources.js`)

One config object per source. Drives BrowseView and DocView generically.

| Key | arxiv | openalex | simplewiki | gutenberg |
|---|---|---|---|---|
| `listEndpoint` | `/arxiv/papers` | `/openalex/works` | `/simplewiki/articles` | `/gutenberg/texts` |
| `detailEndpoint` | `/arxiv/papers/${id}` | `/openalex/works/${id}` | `/simplewiki/articles/${id}` | `/gutenberg/texts/${id}` |
| `contentEndpoint` | `/arxiv/papers/${id}/content` | **null** | `/simplewiki/articles/${id}/content` | `/gutenberg/texts/${id}/content` |
| `chunksEndpoint` | `/arxiv/chunks` | `/openalex/chunks` | `/simplewiki/chunks` | `/gutenberg/chunks` |
| `idField` | `id` | `id` (short W… from API) | `page_id` | `id` |
| `titleField` | `title` | `title` | `title` | `title` |
| `subtitleField` | authors joined | authors joined | *(blank)* | `author` |
| `metaField` | `submitted_date` | `year · venue` | `text_bytes` as KB | `language · release_date` |
| `contentType` | `html` | `none` | `text` | `text` |
| `filters` | q, primary_category, author, submitted_year, has_html, sort | q, author, year, cited_by_min, venue, sort | q, title | title, author, language |

---

## App state (App.js)

```
activeSource  ref<string>        # 'arxiv' | 'openalex' | 'simplewiki' | 'gutenberg'
activeView    ref<string>        # 'browse' | 'doc' | 'chunks'
selectedDoc   ref<object|null>   # the full detail API response
```

Navigation:
- Clicking a source in SourceNav → resets to `browse` view, clears selectedDoc
- Clicking a list item in BrowseView → fetches detail, sets selectedDoc, switches to `doc` view
- "Semantic Search" tab → switches to `chunks` view (query persisted within session)
- "← Back" in DocView/ChunksView → returns to `browse`

---

## BrowseView.js

- Reads `source` config from props
- Local state: `filters` object (one key per filter), `page` (offset), `results`, `total`, `loading`, `error`
- On mount + on filter/page change: calls `api.listDocs(source, filters, limit, offset)`
- Filter bar: renders each filter from `source.filters` config — `text` input, `number` input, `select` dropdown, `boolean` checkbox
- Pagination: prev/next buttons + "showing X–Y of Z"
- List items: title (bold), subtitle (muted), meta (right-aligned), click → emit `select(item)` up to App.js

## DocView.js

- Props: `source`, `doc` (detail object)
- Tabs: **Content** | **Chunks**
- Content tab:
  - Metadata grid: fields defined per source (authors, year, categories, doi, etc.)
  - If `source.contentType === 'html'`: fetch content → render via `innerHTML` in a `.prose` div
  - If `source.contentType === 'text'`: fetch content → render in `<pre class="content-pre">`
  - If `source.contentType === 'none'`: render `doc.abstract` as a paragraph
  - Loading skeleton + error state for content fetch
- Chunks tab: delegates to ChunksView with `source` pre-set and optional seed query from doc title

## ChunksView.js

- Props: `source`
- Local state: `query`, `topK` (default 10), `results`, `loading`, `error`, `usedDense`
- Query input + search button (also fires on Enter)
- `used_dense=false` banner: "Dense search unavailable — Ollama unreachable. Results are keyword-only."
- Results list: each chunk shows section badge, doc_id (clickable — emits `openDoc` event), score, chunk text (truncated to ~400 chars with expand toggle)

---

## CSS design system (`style.css`)

- CSS custom properties on `:root` + `[data-theme="dark"]`
- Light/dark toggle button in top bar (stores preference in `localStorage`)
- Layout: `display: grid; grid-template-columns: 220px 1fr; height: 100vh`
- Sidebar: source chips with active state
- Main area: top bar (source name + view tabs + theme toggle), content area
- Fonts: system-ui for UI, monospace for content/pre areas
- Colors (dark defaults):
  - `--bg`: `#0f1117` / `--surface`: `#1a1d27` / `--border`: `#2d3148`
  - `--text`: `#e2e8f0` / `--muted`: `#94a3b8`
  - `--accent`: `#6366f1`
- No external font imports

---

## api.js

```js
const BASE = '';  // same-origin, relative URLs

async function listDocs(source, filters, limit = 50, offset = 0) { ... }
async function getDoc(source, id) { ... }
async function getContent(source, id) { ... }  // returns raw text/HTML string
async function getChunks(source, query, topK = 10) { ... }
```

All functions throw an object `{ status, message }` on non-2xx so components can show error UI.

---

## Vendor downloads

```bash
mkdir -p frontend/vendor
curl -L "https://cdn.jsdelivr.net/npm/vue@3.5/dist/vue.esm-browser.js" -o frontend/vendor/vue.esm-browser.js
curl -L "https://cdn.jsdelivr.net/npm/marked@15/marked.min.js" -o frontend/vendor/marked.min.js
```

Files are committed to git so no network needed after initial setup.

---

## Verification

1. `source .venv/bin/activate && uvicorn api.main:app --host 0.0.0.0 --port 8002`
2. Open `http://localhost:8002/ui/` — index.html loads
3. Click each source in sidebar — list populates (or shows graceful empty state if DB absent)
4. Apply a filter / paginate — results update
5. Click a result — detail panel loads metadata + content
6. Switch to Chunks tab — run a query — results appear with section badges
7. Toggle dark/light mode — persists on reload
