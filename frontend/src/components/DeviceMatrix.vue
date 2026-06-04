<template>
  <div class="matrix">
    <div v-for="d in devices" :key="d.udid" class="matrix-tile card">
      <div class="matrix-head">
        <div class="matrix-name">
          <strong>{{ d.name || d.udid.slice(0, 12) }}</strong>
          <span class="muted" style="font-size: 0.72rem">{{ d.product_type }} · iOS {{ d.ios_version }}</span>
        </div>
        <div class="row" style="gap: 6px">
          <span class="pill" :class="d.wda_running ? 'on' : 'off'">WDA</span>
          <button class="mx-icon" :title="t('viewMode.expand')" @click="$emit('open', d.udid)">⤢</button>
        </div>
      </div>

      <div class="matrix-body">
        <!-- Controllable (admin, or the normal user who holds this device): the
             live stage with input. -->
        <template v-if="canControl(d.udid)">
          <DeviceStage v-if="d.connected" :udid="d.udid" :connected="true" compact />
          <div v-else class="matrix-connect">
            <button class="primary" :disabled="busy[d.udid]" @click="connectOne(d.udid)">
              {{ busy[d.udid] ? t("detail.connecting") : t("detail.connect") }}
            </button>
          </div>
        </template>

        <!-- Not controllable (a normal user who hasn't claimed this device):
             reservation gate instead of the screen — mirrors Android. They must
             占用 first; control surfaces are never exposed for un-held devices. -->
        <div v-else class="matrix-gate">
          <p class="muted matrix-gate-hint">{{ t("reservation.reserveToControl") }}</p>
          <ReservationCard :udid="d.udid" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, onBeforeUnmount, onMounted } from "vue";
import DeviceStage from "./DeviceStage.vue";
import ReservationCard from "./ReservationCard.vue";
import { useDevices } from "../composables/useDevices";
import { useReservations } from "../composables/useReservations";
import { useUiI18n } from "../composables/useUiI18n";
import { devicesApi } from "../api/devices";

const props = defineProps({ devices: { type: Array, default: () => [] } });
defineEmits(["open"]);

const { t } = useUiI18n();
const { connect } = useDevices();
const { canControl, fetchReservations } = useReservations();
const busy = reactive({});

// Reservation state drives canControl() per tile — make sure it's loaded.
onMounted(fetchReservations);

// Grid renders every device's screen at once → re-encode CPU = resolution ×
// device count. Drop each device to GRID_SCALING% while in the grid; restore to
// 100% (full, sharp) on exit (single/list view) — best-effort, WDA-live. Only
// touch devices the user can actually control (others 403 on the quality POST).
const GRID_SCALING = 70;
const setScaling = (udid, n) => {
  if (canControl(udid)) devicesApi.streamQuality(udid, n).catch(() => {});
};

// Apply 70% to already-connected, controllable devices immediately (setup runs
// pre-mount, so this lands before/with the tiles' first frames).
props.devices.forEach((d) => { if (d.connected) setScaling(d.udid, GRID_SCALING); });

onBeforeUnmount(() => {
  // Leaving the grid → restore full resolution on every controllable device.
  props.devices.forEach((d) => setScaling(d.udid, 100));
});

async function connectOne(udid) {
  if (busy[udid]) return;
  busy[udid] = true;
  try {
    await connect(udid); // brings up WDA; DeviceStage auto-streams once connected
    setScaling(udid, GRID_SCALING); // newly-connected tile also runs downscaled
  } catch (e) {
    /* surfaced via device state / toast elsewhere */
  } finally {
    busy[udid] = false;
  }
}
</script>

<style scoped>
.matrix { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; align-items: start; }
.matrix-tile { display: flex; flex-direction: column; gap: 10px; }
.matrix-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.matrix-name { display: flex; flex-direction: column; min-width: 0; }
.mx-icon { min-height: 30px; width: 32px; padding: 0; font-size: 0.95rem; }
.matrix-connect { display: flex; align-items: center; justify-content: center; min-height: 280px; }
.matrix-gate { display: flex; flex-direction: column; gap: 10px; justify-content: center; min-height: 280px; }
.matrix-gate-hint { font-size: 0.82rem; text-align: center; margin: 0; }
</style>
