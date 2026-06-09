import { ref } from "vue";
import en from "../locales/en/ui.json";
import zhCN from "../locales/zh-CN/ui.json";
import zhTW from "../locales/zh-TW/ui.json";

// Lightweight UI i18n. Public shape mirrors the Android sibling's
// useUiI18n() exactly ({ locale, availableLocales, setLocale, t }) so the
// two front-ends merge by swapping the backing store, not the call sites.
// t(key, params) resolves nested paths and interpolates {param}
// placeholders — identical contract to Android's t(), so locale JSON and
// t() usage transfer verbatim at merge time.
const KEY = "ios-remote-locale";
const MESSAGES = {
  en,
  "zh-CN": zhCN,
  "zh-TW": zhTW,
};
const AVAILABLE = Object.keys(MESSAGES);

function _getPath(object, path) {
  return path.split(".").reduce((acc, key) => {
    if (acc && typeof acc === "object" && key in acc) return acc[key];
    return undefined;
  }, object);
}

function _interpolate(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, key) => {
    const value = params[key];
    return value === undefined || value === null ? "" : String(value);
  });
}

function _read() {
  try {
    const v = window.localStorage.getItem(KEY);
    return AVAILABLE.includes(v) ? v : null;
  } catch {
    return null;
  }
}

const locale = ref(_read() || (navigator.language?.startsWith("zh") ? "zh-CN" : "en"));

// Keep <html lang> in sync so screen readers pick the right pronunciation
// rules and the document advertises its real language.
function _syncHtmlLang(v) {
  try {
    if (document?.documentElement) document.documentElement.lang = v;
  } catch {
    /* ignore (SSR / no DOM) */
  }
}
// Keep the browser tab title (<title>) in sync with the active locale. The
// hardcoded value in index.html is only the pre-mount fallback — once Vue is
// up we own it. Reads `app.title` from the current locale, with English as the
// final fallback so a missing key never wipes the tab title.
function _syncTitle(v) {
  try {
    if (typeof document === "undefined") return;
    const msgs = MESSAGES[v] || MESSAGES.en;
    const title = _getPath(msgs, "app.title") ?? _getPath(MESSAGES.en, "app.title");
    if (title) document.title = String(title);
  } catch {
    /* ignore (SSR / no DOM) */
  }
}
_syncHtmlLang(locale.value);
_syncTitle(locale.value);

export function useUiI18n() {
  function setLocale(v) {
    if (!AVAILABLE.includes(v)) return;
    locale.value = v;
    _syncHtmlLang(v);
    _syncTitle(v);
    try {
      window.localStorage.setItem(KEY, v);
    } catch {
      /* ignore */
    }
  }
  function t(key, params = {}) {
    const current = MESSAGES[locale.value] || MESSAGES.en;
    const resolved = _getPath(current, key) ?? _getPath(MESSAGES.en, key) ?? key;
    return typeof resolved === "string" ? _interpolate(resolved, params) : resolved;
  }
  return { locale, availableLocales: AVAILABLE, setLocale, t };
}
