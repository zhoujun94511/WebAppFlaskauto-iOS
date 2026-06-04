<template>
  <div>
    <div class="page-head">
      <div class="row" style="flex-wrap: wrap">
        <button @click="$emit('back')">{{ t("detail.back") }}</button>
        <h1 style="font-size: 1.05rem">{{ device?.name || udid }}</h1>
        <span v-if="device" class="pill" :class="device.wda_running ? 'on' : 'off'">
          WDA {{ device.wda_running ? t("detail.wdaRunning") : t("detail.wdaDown") }}
        </span>
        <!-- Connection controls live with the WDA status, not in the left column. -->
        <span style="flex: 1 1 auto" />
        <!-- Normal user who hasn't claimed this device: steer them to 占用
             (the ReservationCard) instead of a 连接 button that would 403. -->
        <span v-if="!canCtl" class="pill off">{{ t("reservation.reserveToControl") }}</span>
        <button
          v-else-if="!device?.connected"
          class="primary"
          :disabled="connecting"
          @click="connectDevice"
        >
          {{ connecting ? t("detail.connecting") : t("detail.connect") }}
        </button>
        <button
          v-if="device?.connected"
          class="danger"
          :disabled="disconnecting"
          @click="disconnectDevice"
        >
          {{ disconnecting ? t("detail.disconnecting") : t("detail.disconnect") }}
        </button>
      </div>
    </div>

    <!-- Quick device switcher (only with >1 device) — hop without going back. -->
    <DeviceStrip
      v-if="devices.length > 1"
      :devices="devices"
      :current="udid"
      @select="$emit('select', $event)"
    />

    <div v-if="connecting" class="banner warn">
      {{ t("detail.connectingBanner") }}
    </div>
    <div v-if="err" class="banner err">{{ err }}</div>

    <div class="stage" :class="{ 'with-drawer': showPanels }">
      <!-- LEFT: reservation + device info + connection actions
           (mirrors Android's stage-left: Reservation · QuickInfo · QuickActions) -->
      <div class="stage-left">
        <ReservationCard :udid="udid" />
        <DeviceInfoCard :udid="udid" />
        <QuickActionsCard :udid="udid" />
      </div>

      <!-- CENTER: live screen. The footer "更多" button toggles the control panel. -->
      <DeviceStage
        :udid="udid"
        :connected="!!device?.connected"
        :more-open="showPanels"
        @toggle-more="showPanels = !showPanels"
      />

      <!-- RIGHT control panel — hidden by default, docks as a third column when
           "更多" is pressed (no overlay/page-shift; the device just recenters). -->
      <aside v-show="showPanels" class="panel-drawer" role="region">
        <div class="panel-drawer-head">
        <div class="tabs" role="tablist">
          <button
            v-for="tb in tabs"
            :key="tb.key"
            class="tab"
            :class="{ active: tab === tb.key }"
            role="tab"
            :aria-selected="tab === tb.key"
            @click="tab = tb.key"
          >
            {{ tb.label }}
          </button>
        </div>
        <button class="drawer-close" :title="t('detail.close')" @click="showPanels = false">✕</button>
      </div>
      <!-- v-show (not v-if) keeps panels mounted so typed input, the log
           subscription and fetched lists survive tab switches. -->
      <div class="panel-drawer-body" role="tabpanel">
        <ControlPanel v-show="tab === 'control'" :udid="udid" />
        <AutomationPanel v-show="tab === 'automation'" :udid="udid" />
        <AppsPanel v-show="tab === 'apps'" :udid="udid" />
        <FilesPanel v-show="tab === 'files'" :udid="udid" />
        <LogPanel v-show="tab === 'logs'" :udid="udid" :connected="!!device?.connected" />
      </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import DeviceStage from "../components/DeviceStage.vue";
import DeviceInfoCard from "../components/DeviceInfoCard.vue";
import ReservationCard from "../components/ReservationCard.vue";
import QuickActionsCard from "../components/QuickActionsCard.vue";
import ControlPanel from "../components/ControlPanel.vue";
import AutomationPanel from "../components/AutomationPanel.vue";
import AppsPanel from "../components/AppsPanel.vue";
import FilesPanel from "../components/FilesPanel.vue";
import LogPanel from "../components/LogPanel.vue";
import DeviceStrip from "../components/DeviceStrip.vue";
import { useDevices } from "../composables/useDevices";
import { useReservations } from "../composables/useReservations";
import { useUiI18n } from "../composables/useUiI18n";

const props = defineProps({
  udid: { type: String, required: true },
  // Auto-connect on mount only when the device was explicitly chosen. The
  // default-landing fallback device stays idle until the user hits 连接 — mirrors
  // Android (lands on the stage, but streaming/connect is a manual action).
  autoConnect: { type: Boolean, default: true },
});
const emit = defineEmits(["back", "select"]);

const { t } = useUiI18n();
const { devices, connect, disconnect } = useDevices();
const { canControl } = useReservations();
// May the current user operate this device? Admins bypass; a normal user must
// hold its reservation. Gates auto-connect + the 连接 button so a normal user
// is steered to 占用 first (the ReservationCard) rather than hitting a 403.
const canCtl = computed(() => canControl(props.udid));
const err = ref("");
const connecting = ref(false);
const disconnecting = ref(false);

const showPanels = ref(false); // right control drawer — hidden until "更多" is pressed
const tab = ref("control");
const tabs = computed(() => [
  { key: "control", label: t("control.title") },
  { key: "automation", label: t("automation.title") },
  { key: "apps", label: t("apps.title") },
  { key: "files", label: t("files.title") },
  { key: "logs", label: t("logs.title") },
]);

const device = computed(() => devices.value.find((d) => d.udid === props.udid));

async function connectDevice() {
  if (connecting.value) return;
  if (!canCtl.value) {
    // Normal user without this device's reservation — steer them to 占用.
    err.value = t("reservation.reserveToControl");
    return;
  }
  connecting.value = true;
  err.value = "";
  try {
    // Idempotent: re-checks WDA and only brings it up (go-ios tunnel + runwda)
    // if it isn't already answering. DeviceStage auto-starts the stream once
    // `device.connected` flips true.
    await connect(props.udid);
  } catch (e) {
    err.value = e.message || t("detail.connectFailed");
  } finally {
    connecting.value = false;
  }
}

async function disconnectDevice() {
  if (disconnecting.value) return;
  disconnecting.value = true;
  err.value = "";
  try {
    await disconnect(props.udid); // tears down WDA + forwards on the backend
    emit("back"); // device released → return to the list
  } catch (e) {
    err.value = e.message || t("detail.disconnectFailed");
  } finally {
    disconnecting.value = false;
  }
}

onMounted(() => {
  // Connect (auto-launch tunnel + WDA) only when the device was explicitly
  // opened/selected. The default-landing fallback stays idle to avoid eagerly
  // streaming a device nobody asked for (host-side encode is expensive).
  if (props.autoConnect && canCtl.value && !device.value?.connected) connectDevice();
});
</script>

<style scoped>
/* Control panel — hidden by default; docks as the stage's THIRD column when
   "更多" is pressed (no overlay, no page-shift). The stage simply switches from
   2 to 3 columns and the centered device recenters in the middle. Sticky so it
   stays in view while the page scrolls. */
.panel-drawer {
  position: sticky; top: 16px; align-self: start; min-width: 0;
  max-height: calc(100vh - 110px); display: flex; flex-direction: column;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden;
}
.panel-drawer-head { display: flex; align-items: center; gap: 8px; padding: 10px 12px 0; border-bottom: 1px solid var(--border); }
.panel-drawer-head .tabs { flex: 1; min-width: 0; border-bottom: none; }
.panel-drawer-body {
  flex: 1; padding: 14px;
  /* Reserve the vertical scrollbar gutter at all times so switching between a
     tall tab (scrollbar shown) and a short one (hidden) doesn't shift the
     content sideways — that width change was the "jitter". x hidden kills any
     stray horizontal scrollbar. */
  overflow: hidden auto; scrollbar-gutter: stable;
}
.drawer-close { width: 34px; min-height: 34px; padding: 0; flex: 0 0 auto; margin-bottom: 8px; }
.tabs {
  display: flex; gap: 2px; overflow-x: auto; border-bottom: 1px solid var(--border);
  scrollbar-width: none; /* 5 short tabs fit; hide the bar (still swipe-scrollable if cramped) */
}
.tabs::-webkit-scrollbar { display: none; }
.tab {
  min-height: 38px; flex: 1 1 auto; white-space: nowrap; justify-content: center;
  background: transparent; border: none; border-radius: 8px 8px 0 0;
  color: var(--muted); padding: 8px 10px; font-weight: 500;
  box-shadow: inset 0 -2px 0 transparent;
}
.tab:hover:not(.active) { color: var(--text); background: transparent; }
.tab.active { color: var(--text); font-weight: 650; box-shadow: inset 0 -2px 0 var(--primary); }
</style>
