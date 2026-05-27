/**
 * Multi-select filter dropdown — used by source configs that need a
 * checkbox-style picker (e.g. GeoNames feature class / feature code).
 *
 * Renders as a single button labeled "<label> (N selected) ▾" that toggles
 * a panel of checkboxes. Closes when the user clicks anywhere outside.
 * Emits `update:modelValue` (v-model) with the new array of selected values.
 *
 * Props
 *   label       — the field label shown when no items are selected.
 *   modelValue  — array of currently-selected values (v-model).
 *   options     — array of resolved option objects (parent fetches them).
 *   valueField  — key on each option to use as the selectable value.
 *   labelFn     — function(option) => display label for the checkbox row.
 *   placeholder — text shown when options list is empty (still loading or 0 results).
 */
import { defineComponent, ref, computed, onMounted, onBeforeUnmount } from '/ui/vendor/vue.esm-browser.js';

export default defineComponent({
  name: 'MultiselectFilter',
  props: {
    label:       { type: String, required: true },
    // Default to [] so a parent that hasn't initialised its array yet still
    // renders without throwing in `selectedCount`. Vue calls the default
    // factory once per instance.
    modelValue:  { type: Array,  default: () => [] },
    options:     { type: Array,  default: () => [] },
    valueField:  { type: String, required: true },
    labelFn:     { type: Function, required: true },
    placeholder: { type: String, default: 'No options' },
  },
  emits: ['update:modelValue'],

  setup(props, { emit }) {
    const open = ref(false);
    const root = ref(null);

    // Belt-and-braces: even with the prop default, guard against the parent
    // explicitly passing `undefined` after a hot reload or a routing edge case.
    const selected = computed(() => props.modelValue || []);
    const selectedCount = computed(() => selected.value.length);

    const buttonLabel = computed(() => {
      if (selectedCount.value === 0) return `${props.label} (any) ▾`;
      return `${props.label} (${selectedCount.value} selected) ▾`;
    });

    function isChecked(value) {
      return selected.value.includes(value);
    }

    function toggle(value) {
      const next = isChecked(value)
        ? selected.value.filter(v => v !== value)
        : [...selected.value, value];
      emit('update:modelValue', next);
    }

    function clearAll() {
      emit('update:modelValue', []);
    }

    function onDocClick(e) {
      if (!open.value) return;
      if (root.value && !root.value.contains(e.target)) open.value = false;
    }

    onMounted(() => document.addEventListener('mousedown', onDocClick));
    onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick));

    return { open, root, buttonLabel, selectedCount, isChecked, toggle, clearAll };
  },

  template: `
    <div class="multiselect" ref="root">
      <button
        type="button"
        class="multiselect__trigger"
        @click="open = !open"
        :aria-expanded="open"
      >{{ buttonLabel }}</button>
      <div v-if="open" class="multiselect__panel">
        <div v-if="options.length === 0" class="multiselect__empty">{{ placeholder }}</div>
        <ul v-else class="multiselect__list">
          <li
            v-for="opt in options"
            :key="opt[valueField]"
            class="multiselect__item"
            @click="toggle(opt[valueField])"
          >
            <input
              type="checkbox"
              :checked="isChecked(opt[valueField])"
              @click.stop="toggle(opt[valueField])"
            />
            <span class="multiselect__item-label">{{ labelFn(opt) }}</span>
          </li>
        </ul>
        <div v-if="selectedCount > 0" class="multiselect__footer">
          <button type="button" class="multiselect__clear" @click="clearAll">Clear</button>
        </div>
      </div>
    </div>
  `,
});
