<template>
  <button class="pill-btn" type="button" :title="label" :aria-label="label" @click="toggleTheme">
    <svg v-if="theme === 'dark'" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" /><path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" />
    </svg>
    <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
  </button>
</template>

<script setup>
import { computed } from "vue";
import { useTheme } from "../composables/useTheme";
import { useUiI18n } from "../composables/useUiI18n";

const { theme, toggleTheme } = useTheme();
const { t } = useUiI18n();
const label = computed(() => (theme.value === "dark" ? t("preferences.light") : t("preferences.dark")));
</script>

<style scoped>
.pill-btn {
  width: 40px; height: 40px; display: grid; place-items: center; padding: 0;
  border-radius: 999px; border: 1px solid var(--border-strong);
  background: var(--panel); color: var(--muted);
}
.pill-btn:hover { color: var(--text); border-color: var(--primary-2); }
.pill-btn svg { width: 16px; height: 16px; }
</style>
