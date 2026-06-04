// Localize a thrown API error for display. The backend sends a stable `code`
// (UNAUTHORIZED, RATE_LIMITED, …) plus a human message that happens to be in
// Chinese — so we localize BY CODE here and only fall back to the raw message
// when we have no translation. Network/proxy failures (no JSON body → no code)
// map to a friendly localized "network" string instead of "Request failed (500)".
import { useUiI18n } from "./useUiI18n";

export function useApiError() {
  const { t } = useUiI18n();

  function localizeError(err) {
    const code = err?.code;
    // Backend-down / proxy / non-JSON: http.js tags these HTTP_ERROR.
    if (!code || code === "HTTP_ERROR") return t("errors.network");

    const key = `errors.${code}`;
    const msg = t(key, err.detail || {}); // detail supplies {seconds} etc.
    if (msg !== key) return msg; // had a translation for this code

    // Unknown code: prefer the backend's own message, else a generic line.
    return err.message || t("errors.generic");
  }

  return { localizeError };
}
