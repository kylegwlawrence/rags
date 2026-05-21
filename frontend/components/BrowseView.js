import { defineComponent, ref, reactive, onMounted } from '/ui/vendor/vue.esm-browser.js';
import { listDocs } from '/ui/api.js';

const LIMIT = 50;

export default defineComponent({
  name: 'BrowseView',
  props: { source: { type: Object, required: true } },
  emits: ['select'],

  setup(props, { emit }) {
    const filters = reactive({});
    const offset = ref(0);
    const results = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const error = ref(null);

    function initFilters() {
      for (const key of Object.keys(filters)) delete filters[key];
      for (const f of props.source.filters) {
        filters[f.key] = f.type === 'boolean' ? false : '';
      }
    }

    async function load() {
      loading.value = true;
      error.value = null;
      try {
        const page = await listDocs(props.source, filters, LIMIT, offset.value);
        results.value = page.items;
        total.value = page.total;
      } catch (e) {
        error.value = e.message || 'Failed to load';
      } finally {
        loading.value = false;
      }
    }

    function applyFilters() {
      offset.value = 0;
      load();
    }

    function prevPage() {
      if (offset.value > 0) {
        offset.value = Math.max(0, offset.value - LIMIT);
        load();
      }
    }

    function nextPage() {
      if (offset.value + LIMIT < total.value) {
        offset.value += LIMIT;
        load();
      }
    }

    function itemTitle(item) { return item[props.source.titleField] || '(untitled)'; }
    function itemSubtitle(item) { return props.source.subtitle_fn(item); }
    function itemMeta(item) { return props.source.meta_fn(item); }
    function itemId(item) { return item[props.source.idField]; }

    onMounted(() => {
      initFilters();
      load();
    });

    return {
      filters, offset, results, total, loading, error, LIMIT,
      applyFilters, prevPage, nextPage,
      itemTitle, itemSubtitle, itemMeta, itemId,
    };
  },

  template: `
    <div class="browse-view">
      <form class="filter-bar" @submit.prevent="applyFilters">
        <template v-for="f in source.filters" :key="f.key">
          <div v-if="f.type === 'text' || f.type === 'number'" class="filter-bar__field">
            <label class="filter-bar__label">{{ f.label }}</label>
            <input
              :type="f.type"
              v-model="filters[f.key]"
              :placeholder="f.placeholder || ''"
              class="filter-bar__input"
            />
          </div>
          <div v-else-if="f.type === 'select'" class="filter-bar__field">
            <label class="filter-bar__label">{{ f.label }}</label>
            <select v-model="filters[f.key]" class="filter-bar__input" @change="applyFilters">
              <option v-for="opt in f.options" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div v-else-if="f.type === 'boolean'" class="filter-bar__field filter-bar__field--check">
            <label>
              <input type="checkbox" v-model="filters[f.key]" @change="applyFilters" />
              {{ f.label }}
            </label>
          </div>
        </template>
        <button type="submit" class="filter-bar__btn">Search</button>
      </form>

      <div v-if="error" class="browse-state browse-state--error">{{ error }}</div>
      <div v-else-if="loading" class="browse-state">Loading…</div>
      <div v-else-if="results.length === 0" class="browse-state">No results.</div>

      <ul v-else class="doc-list">
        <li
          v-for="item in results"
          :key="itemId(item)"
          class="doc-list__item"
          @click="$emit('select', item)"
        >
          <div class="doc-list__title">{{ itemTitle(item) }}</div>
          <div v-if="itemSubtitle(item)" class="doc-list__subtitle">{{ itemSubtitle(item) }}</div>
          <div v-if="itemMeta(item)" class="doc-list__meta">{{ itemMeta(item) }}</div>
        </li>
      </ul>

      <div v-if="total > LIMIT" class="pagination">
        <button class="pagination__btn" :disabled="offset === 0" @click="prevPage">← Prev</button>
        <span class="pagination__info">
          {{ offset + 1 }}–{{ Math.min(offset + LIMIT, total) }} of {{ total }}
        </span>
        <button class="pagination__btn" :disabled="offset + LIMIT >= total" @click="nextPage">
          Next →
        </button>
      </div>
    </div>
  `,
});
