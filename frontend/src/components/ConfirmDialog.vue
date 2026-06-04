<template>
  <transition name="cd-fade">
    <div v-if="state.open" class="cd-backdrop" @click.self="cancel" @keydown.esc="cancel">
      <div class="cd-box" role="alertdialog" aria-modal="true" :aria-label="state.message">
        <p class="cd-msg">{{ state.message }}</p>
        <div class="cd-actions">
          <button ref="cancelBtn" class="cd-btn" @click="cancel">
            {{ state.cancelText || t("common.cancel") }}
          </button>
          <button class="cd-btn cd-primary" :class="{ danger: state.danger }" @click="confirm">
            {{ state.confirmText || t("common.confirm") }}
          </button>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup>
import { ref, watch, nextTick } from "vue";
import { useConfirm } from "../composables/useConfirm";
import { useUiI18n } from "../composables/useUiI18n";

const { state, confirm, cancel } = useConfirm();
const { t } = useUiI18n();
const cancelBtn = ref(null);

// Focus the (safe) cancel button when opened; Esc/backdrop also cancel.
watch(
  () => state.open,
  (open) => {
    if (open) nextTick(() => cancelBtn.value?.focus());
  }
);
</script>

<style scoped>
.cd-backdrop {
  position: fixed; inset: 0; z-index: 1100;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, 0.5); backdrop-filter: blur(2px); padding: 20px;
}
.cd-box {
  width: 100%; max-width: 360px; padding: 20px 20px 16px;
  background: var(--panel-solid); border: 1px solid var(--border-strong);
  border-radius: var(--radius); box-shadow: var(--shadow);
  animation: cd-in 0.16s ease;
}
.cd-msg {
  margin: 0 0 18px; font-size: 0.92rem; line-height: 1.5; color: var(--text);
  white-space: pre-line;
}
.cd-actions { display: flex; justify-content: flex-end; gap: 10px; }
.cd-btn {
  min-width: 76px; padding: 8px 16px; border-radius: 9px; font-size: 0.86rem;
  cursor: pointer; border: 1px solid var(--border-strong);
  background: var(--panel); color: var(--text);
}
.cd-btn:hover { border-color: var(--text-muted); }
.cd-primary { border-color: #6366f1; background: #6366f1; color: #fff; }
.cd-primary:hover { filter: brightness(1.08); }
.cd-primary.danger { border-color: #ef4444; background: #ef4444; }
@keyframes cd-in { from { opacity: 0; transform: translateY(10px) scale(0.98); } to { opacity: 1; transform: none; } }
.cd-fade-enter-active, .cd-fade-leave-active { transition: opacity 0.15s ease; }
.cd-fade-enter-from, .cd-fade-leave-to { opacity: 0; }
</style>
