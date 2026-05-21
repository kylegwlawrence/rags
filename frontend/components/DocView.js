import { defineComponent, ref, onMounted } from '/ui/vendor/vue.esm-browser.js';
import ChunksView from '/ui/components/ChunksView.js';
import { getContent } from '/ui/api.js';

export default defineComponent({
  name: 'DocView',
  components: { ChunksView },
  props: {
    source: { type: Object, required: true },
    doc:    { type: Object, required: true },
  },
  emits: ['back'],

  setup(props) {
    const activeTab = ref('content');
    const content = ref(null);
    const contentLoading = ref(false);
    const contentError = ref(null);
    const chunkSeedQuery = ref('');

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

    function openChunksTab() {
      chunkSeedQuery.value = props.doc[props.source.titleField] || '';
      activeTab.value = 'chunks';
    }

    onMounted(() => {
      if (props.source.contentType !== 'none') {
        loadContent();
      }
    });

    return {
      activeTab, content, contentLoading, contentError, chunkSeedQuery,
      visibleMetaFields, openChunksTab,
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

      <div v-if="activeTab === 'content'" class="doc-view__content">
        <!-- Metadata -->
        <dl class="meta-grid">
          <template v-for="f in visibleMetaFields()" :key="f.label">
            <dt class="meta-grid__key">{{ f.label }}</dt>
            <dd class="meta-grid__val">{{ f.value(doc) }}</dd>
          </template>
        </dl>

        <!-- Body -->
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

      <div v-else-if="activeTab === 'chunks'" class="doc-view__content">
        <ChunksView
          :source="source"
          :initial-query="chunkSeedQuery"
          :standalone="false"
        />
      </div>
    </div>
  `,
});
