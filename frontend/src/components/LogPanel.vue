<template>
  <div class="card logpanel">
    <div class="row head">
      <strong>
        {{ t("logs.title") }}
        <span class="muted" style="font-size: 0.78rem">· {{ t("logs.device") }}</span>
      </strong>
      <span class="pill" :class="streaming ? 'on' : 'off'">
        {{ streaming ? t("logs.live") : t("logs.paused") }}
      </span>
    </div>
    <div class="row tools">
      <button class="primary" @click="togglePause">{{ streaming ? t("logs.pause") : t("logs.start") }}</button>
      <button @click="copyLines">{{ t("logs.copy") }}</button>
      <button @click="clearLines">{{ t("logs.clear") }}</button>
      <button :disabled="!allLines.length" @click="exportLog">{{ t("logs.export") }}</button>
    </div>

    <div class="row filters">
      <input
        v-model="search"
        :class="{ bad: searchErr }"
        :placeholder="regexOn ? t('logs.regexPlaceholder') : t('logs.searchPlaceholder')"
        style="flex: 2 1 120px"
      />
      <button class="toggle" :class="{ on: regexOn }" :title="t('logs.regex')" @click="regexOn = !regexOn">.*</button>
      <input v-model="proc" :placeholder="t('logs.proc')" style="flex: 1 1 70px" />
      <select v-model="level" :title="t('logs.level')">
        <option value="">{{ t("logs.all") }}</option>
        <option v-for="lv in LEVELS" :key="lv" :value="lv">{{ lv }}</option>
      </select>
    </div>

    <div class="row opts">
      <label class="opt"><input type="checkbox" v-model="showTs" /> {{ t("logs.timestamp") }}</label>
      <label class="opt"><input type="checkbox" v-model="wrap" /> {{ t("logs.wrap") }}</label>
      <span class="muted count">{{ filtered.length }}/{{ allLines.length }}</span>
    </div>

    <div class="logwrap">
      <div ref="listEl" class="loglist" :class="{ nowrap: !wrap }" @scroll="onScroll">
        <div v-for="(l, i) in filtered" :key="i" class="logline" :class="levelClass(l)">
          <span class="msg">{{ display(l) }}</span>
        </div>
        <div v-if="!filtered.length" class="muted" style="padding: 10px">{{ emptyMsg }}</div>
      </div>
      <button v-if="!atBottom" class="jump" :title="t('logs.jumpBottom')" @click="jumpBottom">↓</button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";
import { useToast } from "../composables/useToast";

// Device (phone) syslog over HTTP SSE — GET /api/devices/<udid>/syslog streams
// go-ios `ios syslog` line-by-line. Plain EventSource; no Socket.IO involved.
const props = defineProps({
  udid: { type: String, default: "" },
  connected: { type: Boolean, default: false },
});

const { t } = useUiI18n();
const { push: toast } = useToast();
const LEVELS = ["Default", "Info", "Debug", "Notice", "Error", "Fault"];
const MAX = 2000; // ring buffer
const PREFS_KEY = "ios-remote-log-prefs";
// "Jun  1 22:10:27 iPhone " — leading month/day/time + device name.
const TS_RE = /^[A-Z][a-z]{2}\s+\d+\s+[\d:]+\s+\S+\s+/;
// process name right before "(lib)" / "[pid]".
const PROC_RE = /(?:^|\s)([A-Za-z0-9_.\-]+)(?:\([^)]*\))?\[\d+\]/;

const allLines = ref([]);
const paused = ref(false);
const search = ref("");
const regexOn = ref(false);
const proc = ref("");
const level = ref("");
const wrap = ref(true);
const showTs = ref(true);
const searchErr = ref(false);
const statusMsg = ref("");
const listEl = ref(null);
const atBottom = ref(true);
const live = ref(false); // reactive stream state (es itself is non-reactive)
let es = null;
let buffer = []; // batched between flushes (syslog is a firehose)
let flushTimer = null;
let retryTimer = null;
let reconnects = 0; // consecutive auto-reconnects (churn cap)
let openedAt = 0;
const MAX_RECONNECT = 4;

// ── persisted preferences ─────────────────────────────────────────────
try {
  const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
  if (LEVELS.includes(p.level)) level.value = p.level;
  regexOn.value = !!p.regexOn;
  wrap.value = p.wrap !== false;
  showTs.value = p.showTs !== false;
} catch { /* ignore */ }
watch([level, regexOn, wrap, showTs], () => {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      level: level.value, regexOn: regexOn.value, wrap: wrap.value, showTs: showTs.value,
    }));
  } catch { /* ignore */ }
});

const streaming = computed(() => live.value && !paused.value);

function procOf(line) {
  const m = line.match(PROC_RE);
  return m ? m[1] : "";
}

const filtered = computed(() => {
  let arr = allLines.value;
  if (level.value) arr = arr.filter((l) => l.includes(`<${level.value}>`));
  if (proc.value.trim()) {
    const q = proc.value.trim().toLowerCase();
    arr = arr.filter((l) => procOf(l).toLowerCase().includes(q));
  }
  const q = search.value.trim();
  searchErr.value = false;
  if (q) {
    if (regexOn.value) {
      let re = null;
      try { re = new RegExp(q, "i"); } catch { searchErr.value = true; }
      if (re) arr = arr.filter((l) => re.test(l));
    } else {
      const lq = q.toLowerCase();
      arr = arr.filter((l) => l.toLowerCase().includes(lq));
    }
  }
  return arr;
});

const emptyMsg = computed(() =>
  allLines.value.length ? t("logs.noMatch") : statusMsg.value,
);

function display(l) {
  return showTs.value ? l : l.replace(TS_RE, "");
}
function levelClass(l) {
  if (l.includes("<Error>") || l.includes("<Fault>")) return "lvl-error";
  if (l.includes("<Debug>")) return "lvl-debug";
  return "";
}

function onScroll() {
  const el = listEl.value;
  if (el) atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}
function scrollDown() {
  nextTick(() => {
    const el = listEl.value;
    if (el && atBottom.value) el.scrollTop = el.scrollHeight;
  });
}
function jumpBottom() {
  const el = listEl.value;
  if (el) el.scrollTop = el.scrollHeight;
  atBottom.value = true;
}

// Flush batched lines ~4x/s — keeps the main thread responsive under a firehose.
function flush() {
  if (!buffer.length) return;
  allLines.value.push(...buffer);
  buffer = [];
  if (allLines.value.length > MAX) allLines.value.splice(0, allLines.value.length - MAX);
  scrollDown();
}

function stopStream() {
  if (flushTimer) { clearInterval(flushTimer); flushTimer = null; }
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  buffer = [];
  live.value = false;
  if (es) { es.close(); es = null; }
}

function start() {
  stopStream();
  if (!props.udid) { statusMsg.value = ""; return; }
  if (!props.connected) { statusMsg.value = t("logs.deviceConnectFirst"); return; }
  statusMsg.value = t("logs.deviceWaiting");
  es = new EventSource(`/api/devices/${encodeURIComponent(props.udid)}/syslog`);
  live.value = true;
  es.onopen = () => { live.value = true; openedAt = performance.now(); };
  es.onmessage = (ev) => buffer.push(ev.data);
  es.onerror = () => {
    if (!es || es.readyState !== EventSource.CLOSED) return; // CONNECTING = retrying
    const ranLong = performance.now() - openedAt > 15000;
    stopStream();
    if (!allLines.value.length) { statusMsg.value = t("logs.deviceError"); return; } // fatal (e.g. 403)
    // A long-stable session that drops gets a fresh reconnect budget; a flapping
    // one stops after MAX_RECONNECT so it can't churn go-ios procs forever.
    if (ranLong) reconnects = 0;
    if (!paused.value && reconnects < MAX_RECONNECT) {
      reconnects += 1;
      retryTimer = setTimeout(start, 2000);
    }
    // else: give up quietly — pill shows off, button becomes "Resume".
  };
  flushTimer = setInterval(flush, 250);
}

function togglePause() {
  // Keyed off `streaming` (not `paused`) so it also restarts a stream that
  // ended on its own (e.g. the SSE dropped): then it acts as "resume".
  if (streaming.value) { paused.value = true; stopStream(); }
  else { paused.value = false; reconnects = 0; start(); }
}
function clearLines() {
  allLines.value = [];
}
async function copyLines() {
  const sel = (window.getSelection && window.getSelection().toString()) || "";
  const text = sel.trim() ? sel : filtered.value.map(display).join("\n");
  try { await navigator.clipboard.writeText(text); toast(t("logs.copied")); }
  catch { /* clipboard blocked */ }
}
function exportLog() {
  const blob = new Blob([allLines.value.join("\n")], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `syslog-${props.udid.slice(0, 8)}-${Date.now()}.log`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// Do NOT auto-start the syslog stream — it spawns a go-ios process and an SSE
// connection that would otherwise run untouched for the whole session. The user
// starts it explicitly via the toggle. On device change, just tear down + reset.
watch(
  () => [props.udid, props.connected],
  () => { stopStream(); paused.value = false; reconnects = 0; statusMsg.value = t("logs.startHint"); },
);
onMounted(() => { statusMsg.value = t("logs.startHint"); });
onUnmounted(stopStream);
</script>

<style scoped>
.logpanel { display: flex; flex-direction: column; }
.head { justify-content: space-between; align-items: center; gap: 8px; }
/* Action buttons share one compact row, each flexing to fill evenly. */
.tools { margin-top: 10px; gap: 6px; flex-wrap: nowrap; }
.tools button { flex: 1 1 0; min-width: 0; padding: 6px 8px; min-height: 36px; font-size: 0.84rem; }
.filters { margin-top: 8px; gap: 6px; flex-wrap: wrap; }
.filters input.bad { border-color: var(--danger); }
.toggle { min-width: 34px; padding: 6px 8px; font-family: ui-monospace, Consolas, monospace; }
.toggle.on { background: var(--primary); color: var(--primary-fg); border-color: transparent; }
.opts { margin-top: 8px; gap: 14px; flex-wrap: wrap; }
.opt { display: inline-flex; align-items: center; gap: 5px; font-size: 0.78rem; color: var(--muted); cursor: pointer; }
.opt input[type="checkbox"] { min-height: 0; width: 15px; height: 15px; cursor: pointer; }
.count { font-size: 0.72rem; white-space: nowrap; font-family: ui-monospace, Consolas, monospace; margin-left: auto; }
.logwrap { position: relative; margin-top: 10px; }
.loglist {
  max-height: 280px;
  overflow: auto;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 0.74rem;
  background: var(--inset);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.loglist.nowrap .msg { white-space: pre; }
.logline { padding: 3px 8px; border-bottom: 1px solid var(--border); }
.logline .msg { white-space: pre-wrap; word-break: break-word; }
.lvl-error .msg { color: var(--danger); }
.lvl-debug .msg { color: var(--muted); }
.jump {
  position: absolute; right: 12px; bottom: 12px; width: 34px; height: 34px;
  min-height: 0; padding: 0; border-radius: 999px;
  background: var(--primary); color: var(--primary-fg); border-color: transparent;
  box-shadow: var(--shadow); font-size: 1rem;
}
</style>
