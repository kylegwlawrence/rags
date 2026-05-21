/**
 * Fetch helpers for the datasets API.
 * All functions throw { status, message } on non-2xx responses.
 */

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

/**
 * @param {object} source - source config from sources.js
 * @param {object} filters - key/value filter params (empty string values are omitted)
 * @param {number} limit
 * @param {number} offset
 * @returns {Promise<{items: any[], total: number, limit: number, offset: number}>}
 */
export async function listDocs(source, filters = {}, limit = 50, offset = 0) {
  const params = new URLSearchParams({ limit, offset });
  for (const [k, v] of Object.entries(filters)) {
    if (v !== '' && v !== null && v !== undefined && v !== false) {
      params.set(k, v);
    }
  }
  const resp = await _fetch(`${source.listEndpoint}?${params}`);
  return resp.json();
}

/**
 * @param {object} source
 * @param {string|number} id - the document's primary key
 * @returns {Promise<object>}
 */
export async function getDoc(source, id) {
  const resp = await _fetch(source.detailEndpoint(id));
  return resp.json();
}

/**
 * @param {object} source
 * @param {string|number} id
 * @returns {Promise<string>} - raw HTML or plain text
 */
export async function getContent(source, id) {
  const resp = await _fetch(source.contentEndpoint(id));
  return resp.text();
}

/**
 * @param {object} source
 * @param {string} query
 * @param {number} topK
 * @returns {Promise<{items: Chunk[], used_dense: boolean, top_k: number, candidate_k: number}>}
 */
export async function getChunks(source, query, topK = 10) {
  const params = new URLSearchParams({ q: query, top_k: topK });
  const resp = await _fetch(`${source.chunksEndpoint}?${params}`);
  return resp.json();
}

/**
 * Fetch all stored chunks for a specific document, ordered by chunk_index.
 * Returns [] if the document has not been indexed.
 *
 * @param {object} source
 * @param {string|number} docId
 * @returns {Promise<StoredChunk[]>}
 */
export async function getDocChunks(source, docId) {
  const params = new URLSearchParams({ doc_id: String(docId) });
  const resp = await _fetch(`${source.docChunksEndpoint}?${params}`);
  return resp.json();
}

/**
 * Embed a single document into its RAG database on demand (live, synchronous).
 * Only available for sources whose config defines `embedEndpoint`.
 *
 * @param {object} source
 * @param {string|number} id - the document's primary key
 * @returns {Promise<{doc_id: string, title: string, chunk_count: number, embedded: boolean}>}
 */
export async function embedDoc(source, id) {
  const resp = await _fetch(source.embedEndpoint(id), { method: 'POST' });
  return resp.json();
}
