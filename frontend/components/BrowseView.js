import { defineComponent, ref, reactive, onMounted, watch } from '/ui/vendor/vue.esm-browser.js';
import { listDocs, getJson } from '/ui/api.js';
import MultiselectFilter from '/ui/components/MultiselectFilter.js';

const LIMIT = 20;

export default defineComponent({
  name: 'BrowseView',
  components: { MultiselectFilter },
  props: { source: { type: Object, required: true } },
  emits: ['select'],

  setup(props, { emit }) {
    const filters = reactive({});
    // Per-filter resolved option lists for filters with an `optionsEndpoint`
    // (`type === 'multiselect'`, or a `select` populated from the API).
    // Populated by fetchFilterOptions on mount and whenever a parent filter
    // (via dependsOn) changes value.
    const filterOptions = reactive({});
    const offset = ref(0);
    const results = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const error = ref(null);
    // Mobile-only: the filter controls collapse behind a "Filters" toggle.
    const filtersOpen = ref(false);

    function initFilters() {
      for (const key of Object.keys(filters)) delete filters[key];
      for (const key of Object.keys(filterOptions)) delete filterOptions[key];
      for (const f of props.source.filters) {
        if (f.type === 'boolean') filters[f.key] = false;
        else if (f.type === 'multiselect') filters[f.key] = [];
        else filters[f.key] = '';
        if (f.optionsEndpoint) filterOptions[f.key] = [];
      }
    }

    async function fetchFilterOptions(f) {
      if (!f.optionsEndpoint) return;
      try {
        const params = { limit: 200 };
        if (f.dependsOn) params[f.dependsOn] = filters[f.dependsOn];
        const data = await getJson(f.optionsEndpoint, params);
        filterOptions[f.key] = Array.isArray(data) ? data : (data.items || []);
        // Drop any currently-selected value(s) that aren't in the new options
        // (orphaned by a parent-filter change). Silent prune.
        if (f.type === 'multiselect') {
          const allowed = new Set(filterOptions[f.key].map(o => o[f.valueField]));
          filters[f.key] = filters[f.key].filter(v => allowed.has(v));
        } else if (f.type === 'select' && filters[f.key]) {
          const allowed = new Set(filterOptions[f.key].map(o => o[f.valueField]));
          if (!allowed.has(filters[f.key])) filters[f.key] = '';
        }
      } catch (e) {
        console.error(`Failed to load options for ${f.key}:`, e);
        filterOptions[f.key] = [];
      }
    }

    function installCascades() {
      for (const f of props.source.filters) {
        if (!f.optionsEndpoint || !f.dependsOn) continue;
        watch(
          () => filters[f.dependsOn],
          () => fetchFilterOptions(f),
          { deep: true },
        );
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

    function visibleOptions(f) {
      // Select populated from the API: map fetched rows to {value, label} and
      // prepend the default "all" option so the filter can be cleared.
      if (f.optionsEndpoint) {
        const rows = filterOptions[f.key] || [];
        const mapped = rows.map(o => ({ value: o[f.valueField], label: f.labelFn(o) }));
        return [{ value: '', label: f.defaultLabel || 'All' }, ...mapped];
      }
      if (!f.dependsOn) return f.options;
      const parentVal = filters[f.dependsOn];
      if (!parentVal) return f.options;
      return f.options.filter(opt => !opt.group || opt.group === parentVal);
    }

    function onSelectChange(f) {
      for (const child of props.source.filters) {
        if (child.type !== 'select' || child.dependsOn !== f.key) continue;
        if (child.optionsEndpoint) {
          // Endpoint-backed child (e.g. arxiv Subcategory): its option list is
          // refetched asynchronously when the parent changes (installCascades
          // watcher), so the current value can't be validated synchronously.
          // Reset it so the search runs with a consistent parent and the child
          // restarts at its default.
          filters[child.key] = '';
        } else {
          // Static-options child: prune synchronously against the new parent.
          const allowed = new Set(visibleOptions(child).map(o => o.value));
          if (!allowed.has(filters[child.key])) filters[child.key] = '';
        }
      }
      applyFilters();
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

    // Initialise filters synchronously in setup so the first render sees the
    // right shape — multiselect filters need `[]` before they're bound to
    // <MultiselectFilter>, whose computed selectedCount would throw on
    // undefined. The previous (working) version of this view only had
    // primitive bindings, so onMounted-time init was fine.
    initFilters();

    onMounted(async () => {
      // Kick off initial option fetches for every filter with an
      // optionsEndpoint (multiselects and API-populated selects). Cascades are
      // installed AFTER the first fetch so the watcher doesn't fire redundantly
      // during init. Don't await on the search — it can run while options are
      // still streaming in.
      const dynamicFilters = props.source.filters.filter(f => f.optionsEndpoint);
      await Promise.all(dynamicFilters.map(fetchFilterOptions));
      installCascades();
      load();
    });

    return {
      filters, filterOptions, offset, results, total, loading, error, LIMIT,
      filtersOpen,
      applyFilters, onSelectChange, visibleOptions, prevPage, nextPage,
      itemTitle, itemSubtitle, itemMeta, itemId,
    };
  },

  template: `
    <div class="browse-view">
      <form class="filter-bar" @submit.prevent="applyFilters">
        <button
          type="button"
          class="filter-bar__toggle"
          :aria-expanded="filtersOpen ? 'true' : 'false'"
          @click="filtersOpen = !filtersOpen"
        >⚙ Filters</button>
        <div class="filter-bar__fields" :class="{ 'filter-bar__fields--open': filtersOpen }">
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
            <select v-model="filters[f.key]" class="filter-bar__input" @change="onSelectChange(f)">
              <option v-for="opt in visibleOptions(f)" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div v-else-if="f.type === 'radio'" class="filter-bar__field">
            <span class="filter-bar__label">{{ f.label }}</span>
            <div class="filter-bar__radios" role="radiogroup" :aria-label="f.label">
              <label v-for="opt in f.options" :key="opt.value" class="filter-bar__radio">
                <input
                  type="radio"
                  :name="'radio-' + f.key"
                  :value="opt.value"
                  v-model="filters[f.key]"
                  @change="applyFilters"
                />
                <span>{{ opt.label }}</span>
              </label>
            </div>
          </div>
          <div v-else-if="f.type === 'boolean'" class="filter-bar__field filter-bar__field--check">
            <label>
              <input type="checkbox" v-model="filters[f.key]" @change="applyFilters" />
              {{ f.label }}
            </label>
          </div>
          <div v-else-if="f.type === 'multiselect'" class="filter-bar__field">
            <label class="filter-bar__label">{{ f.label }}</label>
            <MultiselectFilter
              :label="f.label"
              :model-value="filters[f.key]"
              @update:model-value="filters[f.key] = $event"
              :options="filterOptions[f.key] || []"
              :value-field="f.valueField"
              :label-fn="f.labelFn"
              :placeholder="f.placeholder || 'No options'"
            />
          </div>
        </template>
        </div>
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
