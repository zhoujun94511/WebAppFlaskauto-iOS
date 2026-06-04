<template>
  <div class="toasts" aria-live="polite" aria-atomic="false">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type" role="status">
      {{ t.text }}
    </div>
  </div>
</template>

<script setup>
import { useToast } from "../composables/useToast";
const { toasts } = useToast();
</script>

<style scoped>
.toasts {
  position: fixed; right: 16px; bottom: 16px; z-index: 1000;
  display: flex; flex-direction: column; gap: 8px; pointer-events: none;
}
.toast {
  min-width: 150px; max-width: 320px; padding: 10px 14px; border-radius: 10px;
  font-size: 0.84rem; color: var(--text);
  background: var(--panel-solid); border: 1px solid var(--border-strong);
  box-shadow: var(--shadow); animation: toast-in .18s ease;
}
.toast.ok { border-color: rgba(52, 211, 153, 0.5); }
.toast.err { border-color: rgba(248, 113, 113, 0.5); }
@keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
</style>
