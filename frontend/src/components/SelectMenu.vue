<template>
  <div class="sel" ref="root">
    <button type="button" class="sel-btn" :disabled="disabled" @click="toggle">
      <span class="sel-label" :class="{ ph: !selectedLabel }">{{ selectedLabel || placeholder }}</span>
      <svg class="sel-caret" :class="{ up: open }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
    </button>
    <Teleport to="body">
      <div v-if="open" ref="menu" class="sel-menu" :style="menuStyle" role="listbox">
        <button
          v-for="o in options"
          :key="o.value"
          type="button"
          class="sel-opt"
          :class="{ active: o.value === modelValue }"
          role="option"
          :aria-selected="o.value === modelValue"
          @click="pick(o.value)"
        >
          <span class="sel-check">{{ o.value === modelValue ? "✓" : "" }}</span>
          <span class="sel-opt-label">{{ o.label }}</span>
        </button>
        <div v-if="!options.length" class="sel-empty">{{ emptyText }}</div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from "vue";

const props = defineProps({
  modelValue: { type: [String, Number], default: "" },
  options: { type: Array, default: () => [] }, // [{ value, label }]
  placeholder: { type: String, default: "" },
  emptyText: { type: String, default: "—" },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue"]);

const root = ref(null);
const menu = ref(null);
const open = ref(false);
const menuStyle = ref({});

const selectedLabel = computed(
  () => props.options.find((o) => o.value === props.modelValue)?.label || "",
);

function position() {
  const el = root.value;
  if (!el) return;
  const r = el.getBoundingClientRect();
  const below = window.innerHeight - r.bottom;
  // Open downward by default; flip up if there's clearly more room above.
  const maxH = Math.max(160, Math.min(300, (below > 220 ? below : r.top) - 16));
  const up = below < 220 && r.top > below;
  menuStyle.value = {
    position: "fixed",
    left: `${Math.round(r.left)}px`,
    width: `${Math.round(r.width)}px`,
    maxHeight: `${Math.round(maxH)}px`,
    ...(up ? { bottom: `${Math.round(window.innerHeight - r.top + 4)}px` } : { top: `${Math.round(r.bottom + 4)}px` }),
  };
}

function onDocClick(e) {
  if (!open.value) return;
  if (root.value?.contains(e.target) || menu.value?.contains(e.target)) return;
  close();
}
function onKey(e) { if (e.key === "Escape") close(); }
function onReposition() { if (open.value) position(); }

function bind() {
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", onReposition);
  window.addEventListener("scroll", onReposition, true);
}
function unbind() {
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", onReposition);
  window.removeEventListener("scroll", onReposition, true);
}

function toggle() {
  if (props.disabled) return;
  open.value ? close() : openMenu();
}
function openMenu() {
  position();
  open.value = true;
  bind();
}
function close() {
  open.value = false;
  unbind();
}
function pick(v) {
  emit("update:modelValue", v);
  close();
}
onUnmounted(unbind);
</script>

<style scoped>
.sel { position: relative; display: inline-flex; min-width: 0; }
.sel-btn {
  width: 100%; min-height: 40px; padding: 6px 10px 6px 12px; gap: 8px;
  display: inline-flex; align-items: center; justify-content: space-between;
  border: 1px solid var(--border-strong); border-radius: 9px;
  background: var(--inset); color: var(--text); font: inherit; cursor: pointer;
}
.sel-btn:hover:not(:disabled) { border-color: var(--primary-2); }
.sel-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.sel-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sel-label.ph { color: var(--muted); }
.sel-caret { width: 16px; height: 16px; flex: 0 0 auto; color: var(--muted); transition: transform 0.15s; }
.sel-caret.up { transform: rotate(180deg); }
</style>

<!-- Menu is teleported to <body>; style it globally (scoped wouldn't reach it). -->
<style>
.sel-menu {
  z-index: 1100; overflow-y: auto; padding: 5px;
  background: var(--panel-solid); border: 1px solid var(--border-strong);
  border-radius: 10px; box-shadow: var(--shadow); backdrop-filter: blur(8px);
}
.sel-opt {
  width: 100%; min-height: 0; display: flex; align-items: center; gap: 8px;
  padding: 7px 9px; border: none; background: transparent; color: var(--text);
  border-radius: 7px; font: inherit; text-align: left; cursor: pointer;
}
.sel-opt:hover { background: var(--surface-2); }
.sel-opt.active { background: rgba(59, 130, 246, 0.16); color: var(--text-strong); }
.sel-opt .sel-check { width: 14px; flex: 0 0 auto; color: var(--primary); font-size: 0.8rem; }
.sel-opt .sel-opt-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.82rem; }
.sel-empty { padding: 10px; color: var(--muted); font-size: 0.8rem; text-align: center; }
</style>
