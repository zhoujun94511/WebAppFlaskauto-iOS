<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <strong>{{ t("apps.title") }} <span v-if="apps.length" class="muted" style="font-size: 0.78rem">· {{ apps.length }}</span></strong>
      <div class="row">
        <button :disabled="loading" @click="refresh">{{ loading ? "…" : t("apps.refresh") }}</button>
        <button :disabled="installing" @click="$refs.file.click()">
          {{ installing ? t("apps.installing") : t("apps.installIpa") }}
        </button>
        <input ref="file" type="file" accept=".ipa" style="display: none" @change="onPick" />
      </div>
    </div>

    <p v-if="error" class="banner err" style="margin-top: 8px">{{ error }}</p>

    <div class="applist">
      <div v-for="a in apps" :key="a.bundle_id" class="approw">
        <div class="appmeta">
          <div class="appname">{{ a.name }} <span class="muted">{{ a.version }}</span></div>
          <div class="muted bundle">{{ a.bundle_id }}</div>
        </div>
        <div class="row">
          <button @click="launch(a.bundle_id)">{{ t("apps.launch") }}</button>
          <button class="danger" :disabled="busy === a.bundle_id" @click="uninstall(a)">
            {{ busy === a.bundle_id ? "…" : t("apps.uninstall") }}
          </button>
        </div>
      </div>
      <div v-if="!apps.length && !loading" class="muted" style="padding: 8px">
        {{ t("apps.none") }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { appsApi } from "../api/apps";
import { useControl } from "../composables/useControl";
import { useUiI18n } from "../composables/useUiI18n";
import { useConfirm } from "../composables/useConfirm";

const props = defineProps({ udid: { type: String, required: true } });
const { t } = useUiI18n();
const { ask } = useConfirm();
const { launch: launchApp } = useControl(props.udid);

const apps = ref([]);
const loading = ref(false);
const installing = ref(false);
const busy = ref("");
const error = ref("");

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const d = await appsApi.list(props.udid);
    apps.value = (d.apps || []).sort((a, b) => a.name.localeCompare(b.name));
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function launch(bundleId) {
  launchApp(bundleId);
}

async function uninstall(a) {
  if (!(await ask(t("apps.confirmUninstall", { name: a.name, bundle: a.bundle_id })))) return;
  busy.value = a.bundle_id;
  error.value = "";
  try {
    await appsApi.uninstall(props.udid, a.bundle_id);
    apps.value = apps.value.filter((x) => x.bundle_id !== a.bundle_id);
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}

async function onPick(ev) {
  const file = ev.target.files?.[0];
  ev.target.value = ""; // allow re-picking the same file
  if (!file) return;
  installing.value = true;
  error.value = "";
  try {
    await appsApi.install(props.udid, file);
    await refresh();
  } catch (e) {
    error.value = e.message;
  } finally {
    installing.value = false;
  }
}

onMounted(refresh);
</script>

<style scoped>
.applist { margin-top: 10px; max-height: 320px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.approw {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 6px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--inset);
}
.appmeta { min-width: 0; flex: 1 1 auto; }
.appname { font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bundle { font-size: 0.72rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
/* Keep the action buttons on one line — the meta column shrinks (ellipsis)
   instead of squeezing the buttons into one-glyph-per-line vertical text. */
.approw > .row { flex: 0 0 auto; }
.approw button { white-space: nowrap; }
</style>
