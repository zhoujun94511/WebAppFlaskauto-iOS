import { ref } from "vue";

// Tiny global toast queue for transient success/error feedback. Many actions
// (clipboard push, screenshot, reserve/release, launch…) used to succeed
// silently; push() surfaces a brief, auto-dismissing notice.
const toasts = ref([]); // { id, text, type: 'ok' | 'err' }
let _id = 0;

export function useToast() {
  function push(text, type = "ok") {
    const id = ++_id;
    toasts.value.push({ id, text, type });
    setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id);
    }, 2600);
    return id;
  }
  return { toasts, push };
}
