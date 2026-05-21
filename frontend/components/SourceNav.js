import { defineComponent } from '/ui/vendor/vue.esm-browser.js';

export default defineComponent({
  name: 'SourceNav',
  props: {
    sources: { type: Object, required: true },
    order:   { type: Array,  required: true },
    active:  { type: String, required: true },
    theme:   { type: String, required: true },
  },
  emits: ['select', 'toggle-theme'],
  template: `
    <nav class="sidebar">
      <div class="sidebar__header">
        <h1 class="sidebar__logo">datasets</h1>
      </div>
      <ul class="source-nav">
        <li
          v-for="key in order"
          :key="key"
          class="source-nav__item"
          :class="{ 'source-nav__item--active': key === active }"
          @click="$emit('select', key)"
        >
          <span class="source-nav__label">{{ sources[key].label }}</span>
          <span class="source-nav__sub">{{ sources[key].subtitle }}</span>
        </li>
      </ul>
      <div class="sidebar__footer">
        <button
          class="theme-toggle"
          @click="$emit('toggle-theme')"
          :title="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
        >{{ theme === 'dark' ? '☀' : '☾' }}</button>
      </div>
    </nav>
  `,
});
