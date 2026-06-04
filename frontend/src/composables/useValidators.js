// Client-side auth field validation — mirrors services/request_validators.py so
// registration gives instant feedback before hitting the backend (which still
// re-validates authoritatively). Returns a localized error string, or "" when ok.
import { useUiI18n } from "./useUiI18n";

const USERNAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function useValidators() {
  const { t } = useUiI18n();

  const validateUsername = (name) =>
    USERNAME_RE.test((name || "").trim()) ? "" : t("login.usernameRule");

  const validateEmail = (email) => {
    const e = (email || "").trim();
    return e.length <= 254 && EMAIL_RE.test(e) ? "" : t("login.emailRule");
  };

  const validatePassword = (pw) => {
    const p = pw || "";
    const ok = p.length >= 8 && p.length <= 128 && /[A-Za-z]/.test(p) && /\d/.test(p);
    return ok ? "" : t("login.passwordRule");
  };

  return { validateUsername, validateEmail, validatePassword };
}
