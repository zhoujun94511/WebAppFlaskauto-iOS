<template>
  <div class="auth-screen">
    <div class="auth-toolbar">
      <LanguageSwitcher />
      <ThemeToggle />
    </div>

    <div class="auth-card">
      <div class="auth-brand">
        <div class="auth-brand-lockup">
          <!-- Brand mark (single source: frontend/public/logo.svg) — an iOS
               device showing the Apple logo = "remote control of an iOS device". -->
          <img class="auth-logo" src="/logo.svg" alt="" width="40" height="40" />
          <h1>{{ t("app.name") }}</h1>
        </div>
        <p>{{ t("app.subtitle") }}</p>
      </div>

      <div class="auth-tabs">
        <button :class="{ active: mode === 'login' }" @click="switchMode('login')">
          {{ t("login.login") }}
        </button>
        <button :class="{ active: mode === 'register' }" @click="switchMode('register')">
          {{ t("login.registerBtn") }}
        </button>
      </div>

      <!-- novalidate: suppress the browser's native validation bubble (it
           animates/zooms and can't be styled). We validate in JS and show a
           static inline error instead. -->
      <form class="auth-form" novalidate @submit.prevent="onSubmit">
        <label>
          <span>{{ t("login.username") }}</span>
          <input v-model.trim="username" type="text" name="username" autocomplete="username"
                 :placeholder="t('login.usernamePlaceholder')" />
        </label>

        <label v-if="mode === 'register'">
          <span>{{ t("login.email") }}</span>
          <input v-model.trim="email" type="email" name="email" autocomplete="email"
                 :placeholder="t('login.emailPlaceholder')" />
        </label>

        <label>
          <span>{{ t("login.password") }}</span>
          <input v-model="password" type="password" name="password"
                 :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
                 :placeholder="t('login.passwordPlaceholder')" />
        </label>

        <p v-if="error" class="auth-error">{{ error }}</p>
        <button v-if="error && mode === 'login'" type="button" class="auth-switch-link" @click="switchMode('register')">
          {{ t("login.toRegister") }}
        </button>
        <p v-if="notice" class="auth-notice">{{ notice }}</p>

        <button class="auth-submit" type="submit" :disabled="busy || rateLeft > 0">
          <span v-if="busy" class="auth-spinner" aria-hidden="true" />
          <span>{{ busy ? t("login.working") : submitLabel }}</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from "vue";
import { useAuth } from "../composables/useAuth";
import { useUiI18n } from "../composables/useUiI18n";
import { useApiError } from "../composables/useApiError";
import { useValidators } from "../composables/useValidators";
import ThemeToggle from "./ThemeToggle.vue";
import LanguageSwitcher from "./LanguageSwitcher.vue";

const { t } = useUiI18n();
const { login, register } = useAuth();
const { localizeError } = useApiError();
const { validateUsername, validateEmail, validatePassword } = useValidators();

const mode = ref("login");
const username = ref("");
const email = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");

// Rate-limit lockout: seconds remaining, ticking down live (the backend sends a
// one-shot `detail.seconds`; a static toast just froze that number). While > 0
// the submit button is disabled and the error line re-renders each second.
const rateLeft = ref(0);
let rateTimer = null;
function clearRate() {
  clearInterval(rateTimer);
  rateTimer = null;
  rateLeft.value = 0;
}
function startRateLimit(seconds) {
  clearRate();
  rateLeft.value = Math.max(1, Math.ceil(Number(seconds) || 0));
  error.value = t("errors.RATE_LIMITED", { seconds: rateLeft.value });
  rateTimer = setInterval(() => {
    rateLeft.value -= 1;
    if (rateLeft.value <= 0) {
      clearRate();
      error.value = ""; // lock elapsed — let them try again
    } else {
      error.value = t("errors.RATE_LIMITED", { seconds: rateLeft.value });
    }
  }, 1000);
}
onUnmounted(clearRate);

const submitLabel = computed(() =>
  mode.value === "login" ? t("login.login") : t("login.registerBtn"),
);

function switchMode(next) {
  mode.value = next;
  notice.value = "";
  // Keep an active lockout countdown visible; only clear other errors.
  if (rateLeft.value <= 0) error.value = "";
}

async function onSubmit() {
  if (rateLeft.value > 0) return; // locked out — wait for the countdown
  error.value = "";
  notice.value = "";
  // Custom (static) validation in place of the native bubble.
  if (!username.value.trim() || !password.value) {
    error.value = t("login.fillAllFields");
    return;
  }
  // Registration enforces the full format/strength policy client-side for
  // instant feedback (the backend re-checks via services/request_validators.py).
  // Login only needs non-empty — legacy accounts shouldn't be re-judged.
  if (mode.value === "register") {
    const formErr =
      validateUsername(username.value) ||
      validateEmail(email.value) ||
      validatePassword(password.value);
    if (formErr) {
      error.value = formErr;
      return;
    }
  }
  busy.value = true;
  try {
    if (mode.value === "login") {
      await login(username.value.trim(), password.value);
      // useAuth state flips -> App.vue swaps in the app tree automatically.
    } else {
      const d = await register(username.value.trim(), email.value.trim(), password.value);
      notice.value = d.message || t("login.registerOk");
      mode.value = "login";
      password.value = "";
    }
  } catch (err) {
    if (err?.code === "RATE_LIMITED") {
      startRateLimit(err.detail?.seconds); // drive a live countdown
    } else {
      error.value = localizeError(err);
    }
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.auth-screen {
  position: relative;
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
}

/* Restrained ambience: two soft brand-coloured glows behind the card. Static
   on purpose — drifting motion behind the form is disorienting. */
.auth-screen::before,
.auth-screen::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  background: var(--primary);
  filter: blur(90px);
  opacity: 0.22;
  pointer-events: none;
  z-index: 0;
}
.auth-screen::before { width: 380px; height: 380px; top: -130px; left: -110px; }
.auth-screen::after { width: 320px; height: 320px; right: -110px; bottom: -130px; }

.auth-toolbar {
  position: absolute; top: 18px; right: 18px; z-index: 2;
  display: flex; align-items: center; gap: 10px;
}

.auth-card {
  position: relative; z-index: 1;
  width: min(420px, 100%);
  background: var(--panel-solid);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 32px;
}

.auth-brand { text-align: center; margin-bottom: 24px; }
.auth-brand-lockup { display: flex; align-items: center; justify-content: center; gap: 10px; margin-bottom: 6px; }
.auth-logo { width: 40px; height: 40px; flex: 0 0 auto; border-radius: 9px; }
.auth-brand h1 { font-size: 1.25rem; margin: 0; color: var(--text-strong); }
.auth-brand p { margin: 0; font-size: 0.85rem; color: var(--muted); }

/* Segmented switch — one track with a raised "pill" for the active tab, so it
   reads as a mode toggle rather than two buttons competing with submit. */
.auth-tabs {
  display: flex; gap: 4px; margin-bottom: 22px; padding: 4px;
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px;
}
.auth-tabs button {
  flex: 1; padding: 8px 10px; border: none; border-radius: 9px;
  background: transparent; color: var(--muted); cursor: pointer;
  font-weight: 600; font-size: 0.9rem; transition: background 0.15s ease, color 0.15s ease;
}
.auth-tabs button:hover:not(.active) { color: var(--text); }
/* Active = primary-blue tint (matches the topbar segmented switch / nav / tabs).
   Android's raised-lighter-pill mapping inverts on iOS's dark palette — the
   active panel-solid was DARKER than the track and read as a sunken hole. */
.auth-tabs button.active {
  background: rgba(59, 130, 246, 0.18);
  color: var(--text-strong);
  box-shadow: inset 0 0 0 1px rgba(59, 130, 246, 0.4);
}

.auth-form { display: flex; flex-direction: column; gap: 14px; }
.auth-form label { display: flex; flex-direction: column; gap: 6px; font-size: 0.8rem; color: var(--muted); }
.auth-form input {
  padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface-1); color: var(--text); font-size: 0.95rem;
}
.auth-form input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.32); }

.auth-submit {
  margin-top: 6px; padding: 11px; border: none; border-radius: 10px;
  background: var(--primary); color: var(--primary-fg); font-weight: 600; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 8px;
}
.auth-submit:disabled { opacity: 0.6; cursor: progress; }
.auth-spinner {
  width: 15px; height: 15px; border: 2px solid currentColor; border-right-color: transparent;
  border-radius: 50%; animation: auth-spin 0.6s linear infinite;
}
@keyframes auth-spin { to { transform: rotate(360deg); } }

.auth-error {
  margin: 0; padding: 8px 10px; border-radius: 8px;
  background: rgba(248, 113, 113, 0.12); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.32);
  font-size: 0.82rem; text-align: center;
}
/* A real text link, not a button — kill the global button chrome (40px min
   height, 9px radius, tinted hover background) that made a weird "bubble" pill
   appear behind the small text on hover/focus. */
.auth-switch-link {
  align-self: center; margin: -2px 0 0; padding: 0; min-height: 0; border: none;
  background: transparent; color: var(--primary); font-size: 0.82rem; font-weight: 600;
  cursor: pointer; line-height: 1.3; -webkit-tap-highlight-color: transparent;
}
.auth-switch-link:hover:not(:disabled),
.auth-switch-link:active:not(:disabled) { background: transparent; border-color: transparent; transform: none; }
.auth-switch-link:hover { text-decoration: underline; }
.auth-switch-link:focus { outline: none; }
.auth-switch-link:focus-visible { outline: none; text-decoration: underline; }
.auth-notice {
  margin: 0; padding: 8px 10px; border-radius: 8px;
  background: rgba(52, 211, 153, 0.12); color: #6ee7b7; border: 1px solid rgba(52, 211, 153, 0.32);
  font-size: 0.82rem;
}
/* Light theme: the pastel error/notice text has poor contrast on a pale tint —
   use darker readable tones (mirrors the global .banner light overrides). */
[data-theme="light"] .auth-error {
  background: #fef3f2; color: #b42318; border-color: #fecdca;
}
[data-theme="light"] .auth-notice {
  background: #ecfdf3; color: #067647; border-color: #a6f4c5;
}
</style>
