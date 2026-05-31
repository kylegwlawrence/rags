import { defineComponent, ref, shallowRef, computed, provide, onMounted, onUnmounted } from '/ui/vendor/vue.esm-browser.js';
import SourceNav from '/ui/components/SourceNav.js';
import BrowseView from '/ui/components/BrowseView.js';
import DocView from '/ui/components/DocView.js';
import ChunksView from '/ui/components/ChunksView.js';
import { SOURCES, SOURCE_ORDER } from '/ui/sources.js';
import { getDoc } from '/ui/api.js';

// Hash format: #/{sourceKey}  |  #/{sourceKey}/chunks  |  #/{sourceKey}/doc/{encodedDocId}

function parseHash() {
  const raw = location.hash.slice(1) || '/arxiv';
  const parts = raw.replace(/^\//, '').split('/');
  const sourceKey = (parts[0] && SOURCES[parts[0]]) ? parts[0] : 'arxiv';
  const seg = parts[1];
  if (seg === 'chunks') return { sourceKey, view: 'chunks', docId: null };
  if (seg === 'doc') {
    const rawId = parts.slice(2).join('/');
    const docId = rawId ? decodeURIComponent(rawId) : null;
    return { sourceKey, view: docId ? 'doc' : 'browse', docId };
  }
  return { sourceKey, view: 'browse', docId: null };
}

function buildHash(sourceKey, view, docId = null) {
  if (view === 'chunks') return `#/${sourceKey}/chunks`;
  if (view === 'doc' && docId != null) return `#/${sourceKey}/doc/${encodeURIComponent(String(docId))}`;
  return `#/${sourceKey}`;
}

export default defineComponent({
  name: 'App',
  components: { SourceNav, BrowseView, DocView, ChunksView },

  setup() {
    // Initialise synchronously from URL so there's no flash on reload
    const initial = parseHash();
    const activeSourceKey = ref(initial.sourceKey);
    // Doc view needs an async fetch; start as 'browse' and update in onMounted
    const activeView = ref(initial.view === 'doc' ? 'browse' : initial.view);
    const selectedDoc = shallowRef(null);
    const theme = ref(document.documentElement.getAttribute('data-theme') || 'light');

    const activeSource = computed(() => SOURCES[activeSourceKey.value]);

    function pushNav(sourceKey, view, docId = null) {
      const hash = buildHash(sourceKey, view, docId);
      if (location.hash !== hash) history.pushState(null, '', hash);
    }

    function selectSource(key) {
      activeSourceKey.value = key;
      activeView.value = 'browse';
      selectedDoc.value = null;
      pushNav(key, 'browse');
    }

    function openDoc(doc, targetPage = null) {
      // targetPage (PDFs only) deep-links the viewer to a page; stashed on the
      // doc like redirectedFrom so DocView can read it without extra props.
      if (targetPage != null) doc.targetPage = targetPage;
      selectedDoc.value = doc;
      activeView.value = 'doc';
      const docId = doc[activeSource.value.idField];
      pushNav(activeSourceKey.value, 'doc', docId);
    }

    // Called from ChunksView when clicking a doc title — fetches full detail
    // first. targetPage (optional) opens a PDF straight to that page.
    async function openDocById(docId, targetPage = null) {
      try {
        const doc = await getDoc(activeSource.value, String(docId));
        openDoc(doc, targetPage);
      } catch (e) {
        console.error('Failed to fetch doc:', e);
      }
    }

    function goBack() {
      selectedDoc.value = null;
      activeView.value = 'browse';
      pushNav(activeSourceKey.value, 'browse');
    }

    function openChunks() {
      activeView.value = 'chunks';
      pushNav(activeSourceKey.value, 'chunks');
    }

    function toggleTheme() {
      const next = theme.value === 'dark' ? 'light' : 'dark';
      theme.value = next;
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    }

    // Navigate to a redirect's resolved target, tagging the target doc with the
    // title we came from so DocView can show a "Redirected from …" note. The
    // backend fully resolves the chain, so `targetId` is always a real article.
    async function followRedirect(targetId, fromTitle) {
      try {
        const doc = await getDoc(activeSource.value, String(targetId));
        doc.redirectedFrom = fromTitle;
        openDoc(doc);
      } catch (e) {
        console.error('Failed to follow redirect:', e);
      }
    }

    // Apply a parsed route without pushing history (used by popstate handler and
    // initial-load restoration).
    async function applyRoute({ sourceKey, view, docId }) {
      activeSourceKey.value = sourceKey;
      if (view === 'doc' && docId) {
        try {
          const doc = await getDoc(SOURCES[sourceKey], String(docId));
          selectedDoc.value = doc;
          activeView.value = 'doc';
        } catch (e) {
          console.error('Failed to fetch doc for route:', e);
          selectedDoc.value = null;
          activeView.value = 'browse';
          history.replaceState(null, '', `#/${sourceKey}`);
        }
      } else {
        selectedDoc.value = null;
        activeView.value = view;
      }
    }

    function handlePopState() {
      applyRoute(parseHash());
    }

    onMounted(async () => {
      window.addEventListener('popstate', handlePopState);
      // If the initial URL pointed at a doc, fetch it now
      if (initial.view === 'doc' && initial.docId) {
        await applyRoute(initial);
      }
    });

    onUnmounted(() => {
      window.removeEventListener('popstate', handlePopState);
    });

    // Provide openDocById so ChunksView (nested inside DocView) can call it
    // without prop-drilling event emits through multiple levels. followRedirect
    // is consumed by DocView for the same reason.
    provide('openDocById', openDocById);
    provide('followRedirect', followRedirect);

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

        <!--
          keep-alive caches the BrowseView instance (per :key, so each source
          keeps its own) when you open a doc, so Back restores the exact
          filters/page/results you left. include="BrowseView" excludes DocView
          and ChunksView, which should always render fresh. NB: the comment must
          stay OUTSIDE <keep-alive> — KeepAlive disables caching if its slot has
          more than one child node.
        -->
        <keep-alive include="BrowseView">
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
        </keep-alive>
      </div>
    </div>
  `,
});
