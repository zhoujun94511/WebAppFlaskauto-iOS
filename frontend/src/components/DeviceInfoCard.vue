<template>
  <div class="card">
    <div class="quick-card-head">
      <div>
        <div class="qc-model">{{ info?.marketing || info?.name || udid.slice(0, 12) }}</div>
        <div class="qc-sub">
          {{ info?.product_type }}<template v-if="info?.ios_version"> · iOS {{ info.ios_version }}</template>
        </div>
      </div>
      <div class="row" style="gap: 6px">
        <span class="qc-chip conn-usb">USB</span>
        <button class="qc-icon-btn" :title="t('info.refresh')" :disabled="loading" @click="load">↻</button>
      </div>
    </div>

    <p v-if="error" class="banner err" style="margin: 0 0 10px">{{ error }}</p>
    <p v-else-if="loading && !info" class="muted" style="font-size: 0.82rem">{{ t("info.loading") }}</p>

    <template v-if="info">
      <!-- metric rings (only render those the device actually reports) -->
      <div v-if="info.battery || info.storage" class="metric-grid">
        <div v-if="info.battery" class="metric-tile">
          <Ring :pct="info.battery.level" :tone="batteryTone" />
          <div class="metric-meta">
            <div class="mm-label">{{ t("info.battery") }}</div>
            <div class="mm-value">{{ info.battery.level }}%<span v-if="info.battery.charging"> ⚡</span></div>
          </div>
        </div>
        <div v-if="info.storage" class="metric-tile">
          <Ring :pct="storagePct" :tone="storageTone" />
          <div class="metric-meta">
            <div class="mm-label">{{ t("info.storage") }}</div>
            <div class="mm-value">{{ gb(info.storage.used) }} / {{ gb(info.storage.total) }}</div>
          </div>
        </div>
      </div>

      <dl class="info-table">
        <template v-for="row in rows" :key="row.k">
          <dt>{{ row.k }}</dt>
          <dd :class="{ mono: row.mono }">{{ row.v }}</dd>
        </template>
      </dl>
    </template>
  </div>
</template>

<script setup>
import { computed, h, ref, watch } from "vue";
import { devicesApi } from "../api/devices";
import { useUiI18n } from "../composables/useUiI18n";

const props = defineProps({ udid: { type: String, required: true } });
const { t } = useUiI18n();

const info = ref(null);
const loading = ref(false);
const error = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const res = await devicesApi.info(props.udid);
    info.value = res.info || res.data?.info || res;
  } catch (e) {
    error.value = e.message || "failed";
  } finally {
    loading.value = false;
  }
}
watch(() => props.udid, load, { immediate: true });

const storagePct = computed(() =>
  info.value?.storage ? Math.round((info.value.storage.used / info.value.storage.total) * 100) : 0
);
const batteryTone = computed(() => {
  const l = info.value?.battery?.level ?? 100;
  return l < 20 ? "danger" : l < 50 ? "warn" : "ok";
});
const storageTone = computed(() => (storagePct.value > 90 ? "danger" : storagePct.value > 75 ? "warn" : "ok"));

function gb(bytes) {
  return (bytes / 1e9).toFixed(1) + "G";
}

// Localize the lockdown ActivationState ("Activated"/"Unactivated"); pass other
// values through unchanged.
function activationLabel(s) {
  if (!s) return "";
  if (s === "Activated") return t("info.activated");
  if (s === "Unactivated") return t("info.unactivated");
  return s;
}

// Build the info definition-list from present fields only.
const rows = computed(() => {
  const i = info.value || {};
  const r = [];
  const add = (k, v, mono = false) => { if (v) r.push({ k, v, mono }); };
  add(t("info.version"), i.ios_version && `${i.ios_version}${i.build ? " (" + i.build + ")" : ""}`);
  if (i.resolution) add(t("info.resolution"), `${i.resolution.w} × ${i.resolution.h}`);
  add(t("info.cpu"), i.cpu_arch, true);
  add(t("info.modelNumber"), i.model_number, true);
  add(t("info.hardwareModel"), i.hardware_model, true);
  add(t("info.activation"), activationLabel(i.activation_state));
  add(t("info.serial"), i.serial, true);
  add(t("info.udid"), i.udid, true);
  add(t("info.imei"), i.imei, true);
  add(t("info.imei2"), i.imei2, true);
  add(t("info.wifiMac"), i.wifi_mac, true);
  add(t("info.btMac"), i.bt_mac, true);
  add(t("info.ethernetMac"), i.ethernet_mac, true);
  add(t("info.region"), i.region);
  add(t("info.timezone"), i.timezone);
  add(t("info.phone"), i.phone_number, true);
  return r;
});

// Tiny SVG progress ring.
const Ring = (p) => {
  const R = 18, C = 2 * Math.PI * R;
  const off = C * (1 - Math.max(0, Math.min(100, p.pct)) / 100);
  return h("svg", { class: ["metric-ring", "ring-" + p.tone], viewBox: "0 0 44 44" }, [
    h("circle", { cx: 22, cy: 22, r: R, fill: "none", stroke: "rgba(148,163,184,.2)", "stroke-width": 4 }),
    h("circle", {
      cx: 22, cy: 22, r: R, fill: "none", stroke: "currentColor", "stroke-width": 4,
      "stroke-linecap": "round", "stroke-dasharray": C, "stroke-dashoffset": off,
      transform: "rotate(-90 22 22)",
    }),
    h("text", { x: 22, y: 26, "text-anchor": "middle" }, String(Math.round(p.pct)) + "%"),
  ]);
};
</script>

<style scoped>
.qc-icon-btn { min-height: 30px; width: 30px; padding: 0; font-size: 0.9rem; }
</style>
