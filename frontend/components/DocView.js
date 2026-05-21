import { defineComponent, ref, onMounted } from '/ui/vendor/vue.esm-browser.js';
import { getContent, getDocChunks } from '/ui/api.js';

export default defineComponent({
  name: 'DocView',
  props: {
    source: { type: Object, required: true },
    doc:    { type: Object, required: true },
  },
  emits: ['back'],

  setup(props) {
    const activeTab = ref('content');

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
      if (props.source.contentType !== 'none') {
        loadContent();
      }
    });

    return {
      activeTab, content, contentLoading, contentError,
      chunks, chunksLoading, chunksError,
      expandedChunks,
      visibleMetaFields, openChunksTab, toggleExpand,
    };
  },

  template: `
    <div class="doc-view">
      <div class="doc-view__header">
        <button class="doc-view__back" @click="$emit('back')">← Back</button>
        <h2 class="doc-view__title">{{ doc[source.titleField] || '(untitled)' }}</h2>
      </div>

      <div class="doc-view__tabs">
        <button
          class="doc-view__tab"
          :class="{ 'doc-view__tab--active': activeTab === 'content' }"
          @click="activeTab = 'content'"
        >Content</button>
        <button
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
          <p style="line-height: 1.65; margin: 0;">{{ doc.abstract }}</p>
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
