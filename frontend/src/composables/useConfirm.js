import { reactive } from "vue";

// Global in-app confirmation dialog — replaces the browser's native confirm()
// (which renders the ugly "127.0.0.1:5173 says…" box). `ask()` returns a
// Promise<boolean> that resolves true (confirm) / false (cancel). A single
// <ConfirmDialog /> mounted at the app root renders this state.
const state = reactive({
  open: false,
  message: "",
  confirmText: "",
  cancelText: "",
  danger: false,
  _resolve: null,
});

export function useConfirm() {
  function ask(message, opts = {}) {
    // If a dialog is already open, resolve it as cancelled first.
    if (state._resolve) state._resolve(false);
    state.message = message;
    state.confirmText = opts.confirmText || "";
    state.cancelText = opts.cancelText || "";
    state.danger = opts.danger !== false; // confirmations here are destructive by default
    state.open = true;
    return new Promise((resolve) => {
      state._resolve = resolve;
    });
  }

  function settle(result) {
    const r = state._resolve;
    state.open = false;
    state._resolve = null;
    if (r) r(result);
  }

  return { state, ask, confirm: () => settle(true), cancel: () => settle(false) };
}
