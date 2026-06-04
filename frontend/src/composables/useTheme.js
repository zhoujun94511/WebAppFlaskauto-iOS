import { ref } from "vue";

// Light/dark theme, persisted to localStorage and applied via
// document.documentElement.dataset.theme (mirrors the Android sibling).
const THEMES = ["dark", "light"];
const KEY = "ios-remote-theme";

function _read() {
  try {
    const v = window.localStorage.getItem(KEY);
    return THEMES.includes(v) ? v : null;
  } catch {
    return null;
  }
}

function _detect() {
  const saved = _read();
  if (saved) return saved;
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

const theme = ref(_detect());

function _apply(value, persist = true) {
  const resolved = THEMES.includes(value) ? value : "dark";
  theme.value = resolved;
  document.documentElement.dataset.theme = resolved;
  if (persist) {
    try {
      window.localStorage.setItem(KEY, resolved);
    } catch {
      /* ignore */
    }
  }
}

_apply(theme.value, false); // apply on first import (before render)

export function useTheme() {
  function setTheme(v) {
    _apply(v);
  }
  function toggleTheme() {
    _apply(theme.value === "dark" ? "light" : "dark");
  }
  return { theme, themes: THEMES, setTheme, toggleTheme };
}
