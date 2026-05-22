import { defineComponent, ref, shallowRef, computed, provide } from '/ui/vendor/vue.esm-browser.js';
import SourceNav from '/ui/components/SourceNav.js';
import BrowseView from '/ui/components/BrowseView.js';
import DocView from '/ui/components/DocView.js';
import ChunksView from '/ui/components/ChunksView.js';
import { SOURCES, SOURCE_ORDER } from '/ui/sources.js';
import { getDoc } from '/ui/api.js';

export default defineComponent({
  name: 'App',
  components: { SourceNav, BrowseView, DocView, ChunksView },

  setup() {
    const activeSourceKey = ref('arxiv');
    const activeView = ref('browse'); // 'browse' | 'doc' | 'chunks'
    const selectedDoc = shallowRef(null);
    const theme = ref(document.documentElement.getAttribute('data-theme') || 'light');

    const activeSource = computed(() => SOURCES[activeSourceKey.value]);

    function selectSource(key) {
      activeSourceKey.value = key;
      activeView.value = 'browse';
      selectedDoc.value = null;
    }

    function openDoc(doc) {
      selectedDoc.value = doc;
      activeView.value = 'doc';
    }

    // Called from ChunksView when clicking a doc title — fetches full detail first.
    async function openDocById(docId) {
      try {
        const doc = await getDoc(activeSource.value, String(docId));
        openDoc(doc);
      } catch (e) {
        console.error('Failed to fetch doc:', e);
      }
    }

    function goBack() {
      selectedDoc.value = null;
      activeView.value = 'browse';
    }

    function openChunks() {
      activeView.value = 'chunks';
    }

    function toggleTheme() {
      const next = theme.value === 'dark' ? 'light' : 'dark';
      theme.value = next;
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    }

    // Provide openDocById so ChunksView (nested inside DocView) can call it
    // without prop-drilling event emits through multiple levels.
    provide('openDocById', openDocById);

    return {
      activeSourceKey, activeSource, activeView, selectedDoc, theme,
      SOURCE_ORDER, SOURCES,
      selectSource, openDoc, goBack, openChunks, toggleTheme,
    };
  },

  template: `
    <div class="layout">
      <SourceNav
        :sources="SOURCES"
        :order="SOURCE_ORDER"
        :active="activeSourceKey"
        :theme="theme"
        @select="selectSource"
        @toggle-theme="toggleTheme"
      />
      <div id="main">
        <div class="topbar">
          <div class="topbar__left">
            <span class="topbar__source">{{ activeSource.label }}</span>
            <span class="topbar__sep">·</span>
            <span class="topbar__subtitle">{{ activeSource.subtitle }}</span>
          </div>
          <nav class="topbar__tabs">
            <button
              class="topbar__tab"
              :class="{ 'topbar__tab--active': activeView === 'browse' || activeView === 'doc' }"
              @click="goBack"
            >Browse</button>
            <button
              v-if="activeSource.chunksEndpoint"
              class="topbar__tab"
              :class="{ 'topbar__tab--active': activeView === 'chunks' }"
              @click="openChunks"
            >Semantic Search</button>
          </nav>
        </div>

        <BrowseView
          v-if="activeView === 'browse'"
          :key="activeSourceKey"
          :source="activeSource"
          @select="openDoc"
        />
        <DocView
          v-else-if="activeView === 'doc'"
          :key="selectedDoc && selectedDoc[activeSource.idField]"
          :source="activeSource"
          :doc="selectedDoc"
          @back="goBack"
        />
        <ChunksView
          v-else-if="activeView === 'chunks'"
          :source="activeSource"
          :standalone="true"
        />
      </div>
    </div>
  `,
});
