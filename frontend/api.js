// Fetch helpers for the datasets API. All functions throw { status, message } on non-2xx responses.

async function _fetch(url, options) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    let message = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      message = body.detail || message;
    } catch { /* ignore parse errors */ }
    throw { status: resp.status, message };
  }
  return resp;
}

// Build URLSearchParams from an object, repeating array-valued keys (FastAPI list[str]).
// Empty strings, null, undefined, and false are omitted.
function buildParams(obj) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) {
    if (Array.isArray(v)) {
      for (const item of v) {
        if (item !== '' && item !== null && item !== undefined) sp.append(k, item);
      }
    } else if (v !== '' && v !== null && v !== undefined && v !== false) {
      sp.set(k, v);
    }
  }
  return sp;
}

export async function listDocs(source, filters = {}, limit = 20, offset = 0) {
  const params = buildParams({ limit, offset, ...filters });
  const resp = await _fetch(`${source.listEndpoint}?${params}`);
  return resp.json();
}

export async function getJson(path, params = {}) {
  const sp = buildParams(params);
  const qs = sp.toString();
  const resp = await _fetch(qs ? `${path}?${qs}` : path);
  return resp.json();
}

export async function getDoc(source, id) {
  const resp = await _fetch(source.detailEndpoint(id));
  return resp.json();
}

// Returns null when the source has no resolver or the title isn't found (404).
export async function resolveTitle(source, title) {
  if (!source.resolveEndpoint) return null;
  try {
    const resp = await _fetch(source.resolveEndpoint(title));
    return resp.json();
  } catch (e) {
    if (e && e.status === 404) return null;
    throw e;
  }
}

export async function getContent(source, id) {
  const resp = await _fetch(source.contentEndpoint(id));
  return resp.text();
}

export async function getChunks(source, query, topK = 10) {
  const params = new URLSearchParams({ q: query, top_k: topK });
  const resp = await _fetch(`${source.chunksEndpoint}?${params}`);
  return resp.json();
}

export async function getDocChunks(source, docId) {
  const params = new URLSearchParams({ doc_id: String(docId) });
  const resp = await _fetch(`${source.docChunksEndpoint}?${params}`);
  return resp.json();
}

export async function embedDoc(source, id) {
  const resp = await _fetch(source.embedEndpoint(id), { method: 'POST' });
  return resp.json();
}

export async function downloadDoc(source, id) {
  const resp = await _fetch(source.downloadEndpoint(id), { method: 'POST' });
  return resp.json();
}

export async function getValues(source, id, opts = {}) {
  const params = new URLSearchParams({ limit: opts.limit ?? 500 });
  if (opts.country) params.set('country', opts.country);
  if (opts.year) params.set('year', opts.year);
  const resp = await _fetch(`${source.valuesEndpoint(id)}?${params}`);
  return resp.json();
}
