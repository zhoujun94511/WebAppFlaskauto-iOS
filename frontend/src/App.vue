<template>
  <!-- Multi-user: always gated behind login. -->
  <LoginView v-if="auth.state.ready && !auth.state.user" />

  <div v-else-if="auth.state.ready && auth.state.user" class="shell">
    <NavRail v-model="activeNav" :is-admin="auth.isAdmin.value" />

    <div class="shell-main">
      <header class="topbar">
        <h1 style="font-size: 1.05rem; margin: 0; font-weight: 650">
          {{ activeNav === "settings" ? t("nav.settings") : activeNav === "admin" ? t("app.admin") : t("nav.devices") }}
        </h1>
        <div class="row topbar-actions">
          <!-- Two-mode view switch, aligned with Android: 单屏(single) / 网格(grid). -->
          <div
            v-if="activeNav === 'devices'"
            class="seg"
            role="group"
            :aria-label="t('viewMode.single') + ' / ' + t('viewMode.grid')"
          >
            <button class="seg-btn" :class="{ active: viewMode === 'single' }" :aria-pressed="viewMode === 'single'" :title="t('viewMode.single')" @click="viewMode = 'single'">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><line x1="10" y1="18" x2="14" y2="18"/></svg>
              <span>{{ t("viewMode.single") }}</span>
            </button>
            <button class="seg-btn" :class="{ active: viewMode === 'grid' }" :aria-pressed="viewMode === 'grid'" :title="t('viewMode.grid')" @click="viewMode = 'grid'">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
              <span>{{ t("viewMode.grid") }}</span>
            </button>
          </div>
          <button
            v-if="activeNav === 'devices' && viewMode === 'grid'"
            class="ghost"
            :disabled="devicesLoading"
            :title="t('devices.refresh')"
            @click="refreshDevices(true)"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" :class="{ spin: devicesLoading }"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
            <span>{{ t("devices.refresh") }}</span>
          </button>

          <span class="topbar-sep" />

          <span class="pill" :class="auth.isAdmin.value ? 'warn' : 'on'" :title="auth.state.user.username">
            {{ roleLabel }}
          </span>
          <LanguageSwitcher />
          <ThemeToggle />
          <button class="ghost" :title="t('app.logout')" @click="doLogout">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            <span>{{ t("app.logout") }}</span>
          </button>
        </div>
      </header>

      <main class="shell-content">
        <!-- Admin section (own view; admins only) -->
        <AdminPanel v-if="activeNav === 'admin' && auth.isAdmin.value" @close="activeNav = 'devices'" />

        <!-- Settings section -->
        <div v-else-if="activeNav === 'settings'" class="device-grid" style="grid-template-columns: minmax(0, 480px)">
          <div class="card">
            <strong>{{ t("settings.title") }}</strong>
            <div class="block">
              <label class="muted">{{ t("settings.theme") }}</label>
              <ThemeToggle :labeled="true" />
            </div>
            <div class="block">
              <label class="muted">{{ t("settings.language") }}</label>
              <LanguageSwitcher />
            </div>
            <div class="block">
              <label class="muted">{{ t("settings.about") }}</label>
              <div class="muted" style="font-size: 0.82rem">{{ t("app.title") }} · {{ t("app.subtitle") }}</div>
            </div>
          </div>
        </div>

        <!-- Devices section — two modes, aligned with Android: single / grid.
             Single lands on a device stage (auto-focus first) with a DeviceStrip
             to switch; grid is the live multi-device grid. -->
        <template v-else>
          <template v-if="viewMode === 'single'">
            <DeviceDetailView
              v-if="stageUdid"
              :key="stageUdid"
              :udid="stageUdid"
              :auto-connect="!!selectedUdid"
              @back="viewMode = 'grid'"
              @select="focusDevice"
            />
            <div v-else class="card muted" style="text-align: center; padding: 40px 20px">{{ t("devices.none") }}</div>
          </template>
          <DeviceMatrix v-else :devices="devices" @open="openDevice" />
        </template>
      </main>
    </div>
  </div>

  <Toasts />
  <ConfirmDialog />
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import DeviceDetailView from "./views/DeviceDetailView.vue";
import LoginView from "./components/LoginView.vue";
import AdminPanel from "./components/AdminPanel.vue";
import DeviceMatrix from "./components/DeviceMatrix.vue";
import NavRail from "./components/NavRail.vue";
import ThemeToggle from "./components/ThemeToggle.vue";
import LanguageSwitcher from "./components/LanguageSwitcher.vue";
import Toasts from "./components/Toasts.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import { useAuth } from "./composables/useAuth";
import { useReservations } from "./composables/useReservations";
import { useDevices } from "./composables/useDevices";
import { useUiI18n } from "./composables/useUiI18n";

const auth = useAuth();
const { t } = useUiI18n();
const { fetchReservations } = useReservations();
const { devices, loading: devicesLoading, refresh: refreshDevices, checkHealth } = useDevices();
// Two view modes, aligned with Android: "single" (one device's live stage +
// DeviceStrip to switch) | "grid" (live multi-device grid). Default "single".
// Persisted; old saved values ("list"/"matrix") migrate to single/grid.
const VIEW_MODES = ["single", "grid"];
const VIEW_KEY = "ios-remote-viewmode";
const _savedView = (() => { try { return localStorage.getItem(VIEW_KEY); } catch { return null; } })();
const _migrated = _savedView === "matrix" ? "grid" : _savedView; // list → single (default)
const viewMode = ref(VIEW_MODES.includes(_migrated) ? _migrated : "single");
watch(viewMode, (v) => { try { localStorage.setItem(VIEW_KEY, v); } catch { /* ignore */ } });

// Focused device for "single" mode. The stage falls back to the first device
// when nothing explicit is chosen (mirrors Android's stageDeviceId priority).
const selectedUdid = ref("");
const stageUdid = computed(() => {
  if (selectedUdid.value && devices.value.some((d) => d.udid === selectedUdid.value)) {
    return selectedUdid.value;
  }
  return devices.value[0]?.udid || "";
});
const activeNav = ref("devices");

// Map the backend role enum to a localized label.
const ROLE_KEY = { user: "user", admin: "admin", super_admin: "superAdmin" };
const roleLabel = computed(() => t("roles." + (ROLE_KEY[auth.state.user?.role] || "user")));

// Open a device from the list/grid → focus it AND switch to the single stage.
function openDevice(udid) {
  selectedUdid.value = udid;
  viewMode.value = "single";
}
// Switch the focused device without leaving the single stage (DeviceStrip).
function focusDevice(udid) {
  selectedUdid.value = udid;
}

async function doLogout() {
  await auth.logout();
  selectedUdid.value = "";
  activeNav.value = "devices"; // don't leave a non-admin on a stale admin view
}

// Load the device list once authed. This used to live in the (now-removed) list
// view; with single/grid only, App owns the initial fetch so the single stage
// has a device to focus and the grid has tiles.
async function loadDevices() {
  await checkHealth();      // webrtc-enabled flag + reachability
  await refreshDevices(true);
}
watch(() => auth.state.user, (u) => { if (u) { fetchReservations(); loadDevices(); } });

onMounted(async () => {
  await auth.checkAuth();
  if (auth.state.user) { fetchReservations(); loadDevices(); }
  auth.state.ready = true;
});
</script>
