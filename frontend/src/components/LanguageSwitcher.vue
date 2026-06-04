<template>
  <label class="lang" :title="t('preferences.language')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" /><path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
    <select :value="locale" :aria-label="t('preferences.language')" @change="setLocale($event.target.value)">
      <option v-for="opt in availableLocales" :key="opt" :value="opt">{{ labels[opt] || opt }}</option>
    </select>
  </label>
</template>

<script setup>
import { computed } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { locale, availableLocales, setLocale, t } = useUiI18n();
// Each option labelled in its OWN language (locale-independent native names),
// so adding a locale to useUiI18n needs no change here.
const NATIVE = { en: "English", "zh-CN": "简体中文", "zh-TW": "繁體中文" };
const labels = computed(() => NATIVE);
</script>

<style scoped>
.lang {
  display: inline-flex; align-items: center; gap: 6px; height: 40px; padding: 0 6px 0 11px;
  border-radius: 999px; border: 1px solid var(--border-strong); background: var(--panel); color: var(--muted);
}
.lang:focus-within { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(99,102,241,.18); }
.lang svg { width: 15px; height: 15px; flex: none; }
.lang select { border: none; background: transparent; color: var(--text); padding: 4px 4px; box-shadow: none; }
.lang select:focus { outline: none; box-shadow: none; }
</style>
