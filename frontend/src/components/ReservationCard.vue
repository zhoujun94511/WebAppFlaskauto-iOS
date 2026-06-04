<template>
  <div class="resv" :class="stateClass">
    <!-- Free → pick a duration, then claim (mirrors Android's duration picker). -->
    <template v-if="!res">
      <div class="resv-head">
        <span class="pill on">{{ t("reservation.free") }}</span>
      </div>
      <div class="resv-actions">
        <SelectMenu v-model="minutes" :options="durationOptions" style="flex: 1; min-width: 0" />
        <button class="primary" :disabled="busy" @click="doClaim">{{ t("reservation.reserve") }}</button>
      </div>
    </template>

    <template v-else-if="res.is_mine">
      <div class="resv-head">
        <span class="pill on">{{ t("reservation.yours") }} · {{ countdown }}</span>
      </div>
      <div class="resv-actions">
        <SelectMenu v-model="minutes" :options="durationOptions" style="flex: 1; min-width: 0" />
        <button :disabled="busy" @click="doClaim">{{ t("reservation.extend") }}</button>
        <button class="danger" :disabled="busy" @click="doRelease">{{ t("reservation.release") }}</button>
      </div>
    </template>

    <template v-else>
      <div class="resv-head">
        <span class="pill warn">{{ res.username }} · {{ countdown }}</span>
        <button v-if="isAdmin" class="danger mini-btn" :disabled="busy" @click="doRelease">{{ t("reservation.forceRelease") }}</button>
      </div>
    </template>

    <span v-if="error" class="muted err-text">{{ error }}</span>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useReservations } from "../composables/useReservations";
import { useAuth } from "../composables/useAuth";
import { useUiI18n } from "../composables/useUiI18n";
import { useToast } from "../composables/useToast";
import SelectMenu from "./SelectMenu.vue";

const props = defineProps({ udid: { type: String, required: true } });
const { state, get, claim, release } = useReservations();
const { isAdmin } = useAuth();
const { t } = useUiI18n();
const { push: toast } = useToast();

const busy = ref(false);
const error = ref("");
const now = ref(Date.now());
let timer = null;

// Chosen reservation duration (defaults to the server's default, capped at max).
const minutes = ref(state.defaultMinutes || 60);
function fmtDuration(m) {
  return m % 60 === 0 ? `${m / 60} ${t("reservation.hours")}` : `${m} ${t("reservation.minutes")}`;
}
const durationOptions = computed(() => {
  const opts = [15, 30, 60, 120, 240].filter((m) => m <= (state.maxMinutes || 240));
  return (opts.length ? opts : [state.maxMinutes || 240]).map((m) => ({ value: m, label: fmtDuration(m) }));
});

const res = computed(() => get(props.udid));
const stateClass = computed(() => (!res.value ? "free" : res.value.is_mine ? "mine" : "other"));

const countdown = computed(() => {
  if (!res.value) return "";
  const exp = new Date(String(res.value.expires_at).replace(" ", "T")).getTime();
  let s = Math.max(0, Math.floor((exp - now.value) / 1000));
  const m = Math.floor(s / 60);
  s = s % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
});

async function doClaim() {
  busy.value = true;
  error.value = "";
  const wasReserved = !!res.value; // distinguishes Extend from a fresh Reserve
  try {
    await claim(props.udid, minutes.value);
    toast(t(wasReserved ? "toast.extended" : "toast.reserved"));
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function doRelease() {
  busy.value = true;
  error.value = "";
  try {
    await release(props.udid);
    toast(t("toast.released"));
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.resv {
  display: flex; flex-direction: column; gap: 8px;
  padding: 8px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--inset); font-size: 0.78rem;
}
.resv-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.resv-actions { display: flex; align-items: center; gap: 6px; }
.resv-actions button { min-height: 34px; padding: 5px 12px; }
.mini-btn { min-height: 30px; padding: 3px 10px; font-size: 0.72rem; }
.err-text { color: var(--danger); }
</style>
