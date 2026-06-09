<template>
  <!-- Horizontal device switcher shown inside the single-device stage, so you
       can hop between devices without going back to the list (mirrors Android's
       DeviceStrip). Streams stay lazy — switching just re-opens the stage. -->
  <div class="dstrip" role="tablist" :aria-label="t('detail.switchDevice')">
    <button
      v-for="d in devices"
      :key="d.udid"
      class="dstrip-item"
      :class="{ active: d.udid === current }"
      role="tab"
      :aria-selected="d.udid === current"
      :title="d.marketing || d.name || d.udid"
      @click="d.udid !== current && $emit('select', d.udid)"
    >
      <span class="dot" :class="dotClass(d)" />
      <span class="dlabel">{{ d.marketing || d.name || short(d.udid) }}</span>
    </button>
  </div>
</template>

<script setup>
import { useUiI18n } from "../composables/useUiI18n";

defineProps({
  devices: { type: Array, default: () => [] },
  current: { type: String, default: "" },
});
defineEmits(["select"]);
const { t } = useUiI18n();

const short = (u) => (u ? u.slice(0, 8) : "");
// green = connected/streaming, amber = online but not connected, gray = offline.
function dotClass(d) {
  if (d.streaming || d.connected) return "on";
  if (d.online === false || d.connected === false) return "off";
  return "idle";
}
</script>

<style scoped>
.dstrip {
  display: flex; gap: 8px; overflow-x: auto; padding: 2px 0 8px;
  scrollbar-width: thin;
}
.dstrip-item {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px;
  min-height: 34px; padding: 5px 12px; border-radius: 999px;
  border: 1px solid var(--border-strong); background: var(--panel); color: var(--muted);
  font-size: 0.82rem; cursor: pointer; white-space: nowrap;
  transition: border-color .15s, background .15s, color .15s;
}
.dstrip-item:hover { color: var(--text); border-color: var(--primary-2); }
.dstrip-item.active {
  color: var(--text-strong); border-color: rgba(59, 130, 246, 0.45);
  background: rgba(59, 130, 246, 0.16);
}
.dstrip-item .dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; background: var(--muted); }
.dstrip-item .dot.on { background: #34d399; }
.dstrip-item .dot.off { background: #f87171; }
.dstrip-item .dot.idle { background: #fbbf24; }
.dlabel { max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
</style>
