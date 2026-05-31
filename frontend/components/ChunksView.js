import { defineComponent, ref, onMounted, inject } from '/ui/vendor/vue.esm-browser.js';
import { getChunks } from '/ui/api.js';

export default defineComponent({
  name: 'ChunksView',
  props: {
    source:       { type: Object,  required: true },
    initialQuery: { type: String,  default: '' },
    // standalone: true adds overflow scrolling + padding (main semantic-search view).
    // false = embedded inside DocView's content area, which already scrolls.
    standalone:   { type: Boolean, default: false },
  },

  setup(props) {
    const openDocById = inject('openDocById');

    const query = ref(props.initialQuery || '');
    const topK = ref(10);
    const results = ref([]);
    const loading = ref(false);
    const error = ref(null);
    const usedDense = ref(true);
    const expanded = ref(new Set());

    async function search() {
      if (!query.value.trim()) return;
      loading.value = true;
      error.value = null;
      try {
        const resp = await getChunks(props.source, query.value.trim(), topK.value);
        results.value = resp.items;
        usedDense.value = resp.used_dense;
      } catch (e) {
        error.value = e.message || 'Search failed';
        results.value = [];
      } finally {
        loading.value = false;
      }
    }

    function onKeydown(e) {
      if (e.key === 'Enter') search();
    }

    function toggleExpand(id) {
      const next = new Set(expanded.value);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      expanded.value = next;
    }

    function chunkText(chunk) {
      if (expanded.value.has(chunk.chunk_id)) return chunk.text;
      return chunk.text.length > 400 ? chunk.text.slice(0, 400) + '…' : chunk.text;
    }

    function isExpanded(chunk) {
      return expanded.value.has(chunk.chunk_id);
    }

    // PDF chunk sections are page labels ("p. 42"); pull the page number out so
    // clicking the hit can deep-link the viewer to it. null for every other
    // source (whose section is a heading, not a page).
    function pageOf(chunk) {
      const m = /^p\.\s*(\d+)$/.exec(chunk.section || '');
      return m ? Number(m[1]) : null;
    }

    function openHit(chunk) {
      openDocById(chunk.doc_id, pageOf(chunk));
    }

    onMounted(() => {
      if (query.value.trim()) search();
    });

    return {
      query, topK, results, loading, error, usedDense,
      search, onKeydown, toggleExpand, chunkText, isExpanded, openHit,
    };
  },

  template: `
    <div class="chunks-view" :class="{ 'chunks-view--standalone': standalone }">
      <div>
        <div class="input-pill">
          <input
            type="text"
            v-model="query"
            placeholder="Semantic search query…"
            @keydown="onKeydown"
          />
          <button type="button" @click="search" :disabled="loading">▸</button>
        </div>
        <div class="chunks-view__options" style="margin-top: 8px;">
          <label class="chunks-view__opt-label">
            Top K
            <select v-model.number="topK">
              <option v-for="n in [5, 10, 20, 50]" :key="n" :value="n">{{ n }}</option>
            </select>
          </label>
        </div>
      </div>

      <div v-if="!usedDense && results.length > 0" class="sparse-warn">
        ⚠ Dense search unavailable — Ollama unreachable. Results are keyword-only.
      </div>

      <div v-if="loading" class="chunks-state">Searching…</div>
      <div v-else-if="error" class="chunks-state chunks-state--error">{{ error }}</div>
      <div v-else-if="results.length === 0 && query.trim()" class="chunks-state">No results.</div>

      <ul v-if="results.length > 0" class="chunk-list">
        <li v-for="chunk in results" :key="chunk.chunk_id" class="chunk-card">
          <div class="chunk-card__header">
            <span v-if="chunk.section" class="section-badge">{{ chunk.section }}</span>
            <button
              class="chunk-card__doc-link"
              @click="openHit(chunk)"
              :title="chunk.title"
            >{{ chunk.title }}</button>
            <span class="chunk-card__score">{{ chunk.score.toFixed(3) }}</span>
          </div>
          <p class="chunk-card__text">{{ chunkText(chunk) }}</p>
          <button
            v-if="chunk.text.length > 400"
            class="chunk-card__expand"
            @click="toggleExpand(chunk.chunk_id)"
          >{{ isExpanded(chunk) ? 'Show less' : 'Show more' }}</button>
        </li>
      </ul>
    </div>
  `,
});
