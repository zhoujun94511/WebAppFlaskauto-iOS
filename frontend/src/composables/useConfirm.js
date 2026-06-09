import { reactive } from "vue";

// Global in-app confirm / prompt dialog — replaces the browser's native
// confirm()/prompt() (which render the ugly "127.0.0.1:5173 says…" box).
//   ask(msg)          → Promise<boolean>           (confirm mode)
//   askPrompt(opts)   → Promise<string | null>     (text-input mode; null = cancel)
// A single <ConfirmDialog /> mounted at the app root renders both modes from
// this shared state — the dialog branches on `state.mode`.
const state = reactive({
  open: false,
  mode: "confirm", // "confirm" | "prompt"
  message: "",
  confirmText: "",
  cancelText: "",
  danger: false,
  // prompt-mode only:
  value: "",
  placeholder: "",
  password: false,
  _resolve: null,
});

// In confirm mode the cancel sentinel is `false`; in prompt mode it's `null`.
const _cancelValue = () => (state.mode === "prompt" ? null : false);

export function useConfirm() {
  function ask(message, opts = {}) {
    // If a dialog is already open, resolve it as cancelled first.
    if (state._resolve) state._resolve(_cancelValue());
    state.mode = "confirm";
    state.message = message;
    state.confirmText = opts.confirmText || "";
    state.cancelText = opts.cancelText || "";
    state.danger = opts.danger !== false; // confirmations here are destructive by default
    state.value = "";
    state.placeholder = "";
    state.password = false;
    state.open = true;
    return new Promise((resolve) => {
      state._resolve = resolve;
    });
  }

  function askPrompt(opts = {}) {
    if (state._resolve) state._resolve(_cancelValue());
    state.mode = "prompt";
    state.message = opts.message || "";
    state.confirmText = opts.confirmText || "";
    state.cancelText = opts.cancelText || "";
    state.danger = !!opts.danger; // prompts are NOT destructive by default
    state.value = opts.value || "";
    state.placeholder = opts.placeholder || "";
    state.password = !!opts.password;
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

  return {
    state,
    ask,
    askPrompt,
    // confirm() / cancel() are mode-aware: prompt mode returns the input value
    // (or null on cancel); confirm mode returns true / false.
    confirm: () => settle(state.mode === "prompt" ? state.value : true),
    cancel: () => settle(_cancelValue()),
  };
}
