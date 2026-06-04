<template>
  <div class="card">
    <div class="row" style="justify-content: space-between; gap: 8px">
      <strong>{{ t("automation.title") }}</strong>
      <button class="ghost-sm" :title="fg || t('automation.foregroundApp')" @click="loadForeground">
        {{ t("automation.foregroundApp") }}
      </button>
    </div>
    <p v-if="fg" class="muted fg-line" :title="fg">{{ fg }}</p>

    <!-- Selector: strategy + value -->
    <div class="block">
      <label class="muted">{{ t("automation.selectors") }}</label>
      <div class="row">
        <select v-model="using" style="flex: 0 0 auto">
          <option value="accessibility id">accessibility id</option>
          <option value="predicate string">predicate</option>
          <option value="xpath">xpath</option>
          <option value="class name">class name</option>
          <option value="name">name</option>
        </select>
      </div>
      <input v-model="value" :placeholder="t('automation.selectorPlaceholder')" @keyup.enter="find" />
    </div>

    <!-- Actions on the matched element -->
    <div class="block">
      <label class="muted">{{ t("automation.actions") }}</label>
      <div class="row">
        <button @click="find">{{ t("automation.find") }}</button>
        <button class="primary" @click="tap">{{ t("automation.tap") }}</button>
      </div>
    </div>

    <!-- Type text into the matched element -->
    <div class="block">
      <label class="muted">{{ t("automation.typeSection") }}</label>
      <div class="row">
        <input v-model="text" :placeholder="t('automation.typePlaceholder')" style="flex: 1; min-width: 0" @keyup.enter="type" />
        <button @click="type">{{ t("automation.type") }}</button>
      </div>
    </div>

    <p v-if="error" class="banner err">{{ error }}</p>
    <pre v-if="result" class="result">{{ result }}</pre>

    <!-- Page source (formatted XML) -->
    <div class="block">
      <label class="muted">{{ t("automation.pageSource") }}</label>
      <div class="row">
        <button @click="loadSource" :disabled="sourceLoading">
          {{ sourceLoading ? "…" : t("automation.fetchSource") }}
        </button>
        <button v-if="source" @click="source = ''">{{ t("automation.clear") }}</button>
      </div>
      <pre v-if="source" class="result xml">{{ source }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";
import { automationApi } from "../api/automation";
import { useUiI18n } from "../composables/useUiI18n";

const props = defineProps({ udid: { type: String, required: true } });
const { t } = useUiI18n();

const using = ref("accessibility id");
const value = ref("");
const text = ref("");
const result = ref("");
const source = ref("");
const sourceLoading = ref(false);
const fg = ref("");
const error = ref("");

// Pretty-print the flat WDA page-source XML: one tag per line, indented by depth.
// (The backend returns it as a single unbroken string, hence the wall of text.)
function formatXml(xml) {
  if (!xml) return "";
  const PAD = "  ";
  let out = "", depth = 0;
  // Break between adjacent tags, then walk line by line tracking nesting.
  for (let line of xml.replace(/>\s*</g, ">\n<").split("\n")) {
    line = line.trim();
    if (!line) continue;
    if (line.startsWith("</")) depth = Math.max(0, depth - 1);
    out += PAD.repeat(depth) + line + "\n";
    const isOpen =
      line.startsWith("<") &&
      !line.startsWith("</") &&
      !line.startsWith("<?") &&
      !line.startsWith("<!") &&
      !line.endsWith("/>");
    if (isOpen) depth++;
  }
  return out.trimEnd();
}

async function _run(fn) {
  error.value = "";
  try {
    return await fn();
  } catch (e) {
    error.value = e.message;
    return null;
  }
}

async function find() {
  const d = await _run(() => automationApi.find(props.udid, using.value, value.value));
  if (d) result.value = JSON.stringify(d, null, 2);
}
async function tap() {
  const d = await _run(() => automationApi.tap(props.udid, using.value, value.value));
  if (d) result.value = t("automation.tapped", { value: value.value });
}
async function type() {
  const d = await _run(() => automationApi.type(props.udid, using.value, value.value, text.value, true));
  if (d) result.value = t("automation.typed", { n: d.typed });
}
async function loadForeground() {
  const d = await _run(() => automationApi.foreground(props.udid));
  if (d) fg.value = d.bundleId || t("automation.unknown");
}
async function loadSource() {
  sourceLoading.value = true;
  const d = await _run(() => automationApi.source(props.udid));
  sourceLoading.value = false;
  if (d) source.value = d.source ? formatXml(d.source) : t("automation.empty");
}
</script>

<style scoped>
.block { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.fg-line {
  margin: 2px 0 0; font-size: 0.72rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ghost-sm { min-height: 32px; padding: 4px 10px; font-size: 0.8rem; }
.result {
  margin-top: 4px; max-height: 160px; overflow: auto;
  background: var(--inset); border: 1px solid var(--border); border-radius: 8px;
  padding: 8px; font-size: 0.72rem; white-space: pre-wrap; word-break: break-word;
}
/* Formatted XML: keep indentation (no wrap collapse), horizontal scroll for long
   attribute lines, monospace for alignment. */
.result.xml {
  max-height: 280px; white-space: pre; word-break: normal; overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; line-height: 1.5; tab-size: 2;
}
</style>
