import { defineComponent, ref, computed, onMounted } from '/ui/vendor/vue.esm-browser.js';
import { getDoc, getContent, getDocChunks, embedDoc } from '/ui/api.js';

export default defineComponent({
  name: 'DocView',
  props: {
    source: { type: Object, required: true },
    doc:    { type: Object, required: true },
  },
  emits: ['back'],

  setup(props) {
    const activeTab = ref('content');

    // The browse list emits a slim list row (e.g. factbook CountrySummary =
    // id/name/region) that lacks the rich `data` blob the detail endpoint
    // returns. When `source.fetchDetail` is set, pull the full detail so the
    // sectioned profile below has something to render.
    const detail = ref(props.doc);

    // Content tab state
    const content = ref(null);
    const contentLoading = ref(false);
    const contentError = ref(null);

    // Chunks tab state
    const chunks = ref([]);
    const chunksLoading = ref(false);
    const chunksError = ref(null);
    const chunksLoaded = ref(false);
    const expandedChunks = ref(new Set());

    // On-demand embed state (only for sources with an embedEndpoint).
    const embedding = ref(false);
    const embedError = ref(null);

    // "Embedded" means we've confirmed (via the doc-chunks fetch) that the RAG
    // DB holds chunks for this doc. Until that fetch lands, status is unknown
    // and the button stays disabled.
    const isEmbedded = computed(() => chunksLoaded.value && chunks.value.length > 0);

    const embedLabel = computed(() => {
      if (embedding.value) return 'Embedding…';
      if (embedError.value) return 'Retry embed';
      return isEmbedded.value ? 'Re-embed' : 'Embed';
    });

    // ----- Nested-data profile rendering (contentType 'none' sources whose
    // detail returns a `data` object, e.g. factbook). Builds sanitized HTML:
    // every text leaf is escaped, only our own structural tags are injected.
    function fbEscape(s) {
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }

    // Source values carry presentational HTML (<p>, <br>, <strong>); convert
    // structural tags to newlines, drop the rest, collapse whitespace.
    function fbClean(s) {
      return String(s)
        .replace(/<\s*br\s*\/?>/gi, '\n')
        .replace(/<\/\s*p\s*>/gi, '\n')
        .replace(/<[^>]+>/g, '')
        .replace(/\n{3,}/g, '\n\n')
        .replace(/[ \t]+/g, ' ')
        .trim();
    }

    function fbRenderValue(v) {
      if (v === null || v === undefined || v === '') return '';
      if (Array.isArray(v)) {
        const items = v.map((it) => fbRenderValue(it)).filter(Boolean);
        if (!items.length) return '';
        return '<ul class="profile__list">' +
          items.map((it) => `<li>${it}</li>`).join('') + '</ul>';
      }
      if (typeof v === 'object') {
        let rows = '';
        for (const [k, val] of Object.entries(v)) {
          const rendered = fbRenderValue(val);
          if (!rendered) continue;
          rows += `<dt class="profile__key">${fbEscape(k)}</dt>` +
                  `<dd class="profile__val">${rendered}</dd>`;
        }
        return rows ? `<dl class="profile__grid">${rows}</dl>` : '';
      }
      return fbEscape(fbClean(v)).replace(/\n/g, '<br>');
    }

    async function loadDetail() {
      if (!props.source.fetchDetail) return;
      try {
        detail.value = await getDoc(props.source, props.doc[props.source.idField]);
      } catch (e) {
        // Keep the slim list row; the profile just stays empty on failure.
        console.error('Failed to fetch detail:', e);
      }
    }

    const profileHtml = computed(() => {
      const data = detail.value && detail.value.data;
      if (!data || typeof data !== 'object' || Array.isArray(data)) return '';
      let out = '';
      for (const [section, fields] of Object.entries(data)) {
        const body = fbRenderValue(fields);
        if (!body) continue;
        out += `<section class="profile__section"><h3 class="profile__heading">${fbEscape(section)}</h3>${body}</section>`;
      }
      return out ? `<div class="profile">${out}</div>` : '';
    });

    function visibleMetaFields() {
      return props.source.metaFields.filter((f) => {
        const v = f.value(props.doc);
        return v !== null && v !== undefined && v !== '';
      });
    }

    async function loadContent() {
      if (!props.source.contentEndpoint) return;
      contentLoading.value = true;
      contentError.value = null;
      try {
        content.value = await getContent(props.source, props.doc[props.source.idField]);
      } catch (e) {
        contentError.value = e.message || 'Failed to load content';
      } finally {
        contentLoading.value = false;
      }
    }

    async function loadChunks() {
      if (chunksLoaded.value) return;
      chunksLoading.value = true;
      chunksError.value = null;
      try {
        const docId = props.doc[props.source.docIdField || props.source.idField];
        chunks.value = await getDocChunks(props.source, docId);
        chunksLoaded.value = true;
      } catch (e) {
        chunksError.value = e.message || 'Failed to load chunks';
      } finally {
        chunksLoading.value = false;
      }
    }

    function openChunksTab() {
      activeTab.value = 'chunks';
      loadChunks();
    }

    async function embedArticle() {
      embedding.value = true;
      embedError.value = null;
      try {
        await embedDoc(props.source, props.doc[props.source.idField]);
        // Re-fetch so the chunk inspector and the button's embedded state both
        // reflect what was just written.
        chunksLoaded.value = false;
        await loadChunks();
      } catch (e) {
        embedError.value = e.message || 'Embed failed';
      } finally {
        embedding.value = false;
      }
    }

    function toggleExpand(chunkId) {
      if (expandedChunks.value.has(chunkId)) {
        expandedChunks.value.delete(chunkId);
      } else {
        expandedChunks.value.add(chunkId);
      }
      // trigger reactivity
      expandedChunks.value = new Set(expandedChunks.value);
    }

    onMounted(() => {
      loadDetail();
      if (props.source.contentType !== 'none') {
        loadContent();
      }
      // Preload chunks when the source supports embedding so the header button
      // can show the right state (Embed vs Re-embed) without waiting for the
      // Chunks tab to be opened. The query is indexed and cheap.
      if (props.source.embedEndpoint) {
        loadChunks();
      }
    });

    return {
      activeTab, content, contentLoading, contentError,
      chunks, chunksLoading, chunksError, chunksLoaded,
      expandedChunks,
      embedding, embedError, isEmbedded, embedLabel,
      profileHtml,
      visibleMetaFields, openChunksTab, toggleExpand, embedArticle,
    };
  },

  template: `
    <div class="doc-view">
      <div class="doc-view__header">
        <button class="doc-view__back" @click="$emit('back')">← Back</button>
        <h2 class="doc-view__title">{{ doc[source.titleField] || '(untitled)' }}</h2>
        <button
          v-if="source.embedEndpoint"
          class="doc-view__embed"
          :class="{
            'doc-view__embed--done': isEmbedded && !embedding && !embedError,
            'doc-view__embed--error': embedError && !embedding,
          }"
          :disabled="embedding || chunksLoading"
          :title="embedError || (isEmbedded ? 'Already embedded — click to re-embed into semantic search' : 'Embed this article into semantic search')"
          @click="embedArticle"
        >{{ embedLabel }}</button>
      </div>

      <div class="doc-view__tabs">
        <button
          class="doc-view__tab"
          :class="{ 'doc-view__tab--active': activeTab === 'content' }"
          @click="activeTab = 'content'"
        >Content</button>
        <button
          v-if="source.docChunksEndpoint"
          class="doc-view__tab"
          :class="{ 'doc-view__tab--active': activeTab === 'chunks' }"
          @click="openChunksTab"
        >Chunks</button>
      </div>

      <!-- Content tab -->
      <div v-if="activeTab === 'content'" class="doc-view__content">
        <dl class="meta-grid">
          <template v-for="f in visibleMetaFields()" :key="f.label">
            <dt class="meta-grid__key">{{ f.label }}</dt>
            <dd class="meta-grid__val">{{ f.value(doc) }}</dd>
          </template>
        </dl>

        <div v-if="source.contentType === 'none'">
          <div v-if="profileHtml" class="profile-wrap" v-html="profileHtml" />
          <p v-else style="line-height: 1.65; margin: 0;">{{ doc.abstract }}</p>
        </div>
        <div v-else-if="contentLoading" class="doc-content-state">Loading content…</div>
        <div v-else-if="contentError" class="doc-content-state doc-content-state--error">
          {{ contentError }}
        </div>
        <div v-else-if="source.contentType === 'html' && content" class="prose" v-html="content" />
        <pre v-else-if="source.contentType === 'text' && content" class="content-pre">{{ content }}</pre>
      </div>

      <!-- Chunks tab — stored chunk inspector -->
      <div v-else-if="activeTab === 'chunks'" class="doc-view__content">
        <div v-if="chunksLoading" class="doc-content-state">Loading chunks…</div>
        <div v-else-if="chunksError" class="doc-content-state doc-content-state--error">
          {{ chunksError }}
        </div>
        <div v-else-if="chunks.length === 0" class="doc-content-state">
          No chunks indexed for this document.
        </div>
        <div v-else>
          <p class="chunks-summary">{{ chunks.length }} chunk{{ chunks.length === 1 ? '' : 's' }} indexed</p>
          <div class="chunk-list">
            <div
              v-for="chunk in chunks"
              :key="chunk.chunk_id"
              class="chunk-card"
            >
              <div class="chunk-card__header">
                <span v-if="chunk.section" class="section-badge">{{ chunk.section }}</span>
                <span class="chunk-card__index">#{{ chunk.chunk_index }}</span>
                <span class="chunk-card__length">{{ chunk.text_length }} chars</span>
              </div>
              <div class="chunk-card__body">
                <template v-if="expandedChunks.has(chunk.chunk_id)">
                  <pre class="chunk-card__text chunk-card__text--full">{{ chunk.text }}</pre>
                  <button class="chunk-card__toggle" @click="toggleExpand(chunk.chunk_id)">Show less</button>
                </template>
                <template v-else>
                  <pre class="chunk-card__text">{{ chunk.text.length > 400 ? chunk.text.slice(0, 400) + '…' : chunk.text }}</pre>
                  <button
                    v-if="chunk.text.length > 400"
                    class="chunk-card__toggle"
                    @click="toggleExpand(chunk.chunk_id)"
                  >Show more</button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
});
