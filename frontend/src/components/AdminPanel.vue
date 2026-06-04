<template>
  <div class="card">
    <div class="row" style="justify-content: space-between">
      <strong>{{ t("app.admin") }}</strong>
      <button @click="$emit('close')">{{ t("admin.close") }}</button>
    </div>

    <!-- Tabs: user management · device reservations -->
    <div class="tabs" role="tablist">
      <button class="tab" :class="{ active: tab === 'users' }" role="tab" :aria-selected="tab === 'users'" @click="tab = 'users'">
        {{ t("admin.tabUsers") }}
      </button>
      <button class="tab" :class="{ active: tab === 'reservations' }" role="tab" :aria-selected="tab === 'reservations'" @click="switchToReservations">
        {{ t("admin.tabReservations") }}<span v-if="reservations.length" class="muted"> · {{ reservations.length }}</span>
      </button>
    </div>

    <p v-if="error" class="banner err">{{ error }}</p>

    <!-- USERS -->
    <template v-if="tab === 'users'">
      <!-- Create user — kept at the top so it's the first thing you reach. -->
      <div class="create-box">
        <strong style="font-size: 0.85rem">{{ t("admin.createUser") }}</strong>
        <div class="row" style="flex-wrap: wrap; margin-top: 8px">
          <input v-model="nu.username" :placeholder="t('admin.username')" style="flex: 1 1 110px; min-width: 0" />
          <input v-model="nu.email" :placeholder="t('admin.email')" style="flex: 1 1 150px; min-width: 0" />
          <input v-model="nu.password" :placeholder="t('admin.password')" style="flex: 1 1 120px; min-width: 0" />
          <SelectMenu v-model="nu.role" :options="roleOptions" style="flex: 0 0 110px" />
          <button class="primary" @click="create">{{ t("admin.add") }}</button>
        </div>
      </div>

      <table class="utable">
        <thead>
          <tr><th>{{ t("admin.colUser") }}</th><th>{{ t("admin.colEmail") }}</th><th>{{ t("admin.colRole") }}</th><th>{{ t("admin.colActive") }}</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>
              {{ u.username }}
              <span v-if="u.id === meId" class="muted" style="font-size: 0.7rem"> · {{ t("admin.you") }}</span>
            </td>
            <td class="muted">{{ u.email }}</td>
            <td>
              <!-- super_admin can re-rank admin↔user; the super_admin row and
                   your own row are locked. A plain admin sees only users → no
                   role control (they can't promote). -->
              <SelectMenu
                v-if="canEditRole(u)"
                :model-value="u.role"
                :options="rankOptions"
                style="width: 104px"
                @update:model-value="(r) => changeRole(u, r)"
              />
              <span v-else>{{ u.role }}</span>
            </td>
            <td><button class="mini" :disabled="u.role === 'super_admin'" @click="toggleActive(u)">{{ u.is_active ? t("admin.yes") : t("admin.no") }}</button></td>
            <td>
              <button v-if="canDelete(u)" class="mini danger" @click="remove(u)">{{ t("admin.del") }}</button>
              <span v-else class="muted" style="font-size: 0.72rem">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- RESERVATIONS (force-release / kick) -->
    <template v-else>
      <div class="row" style="justify-content: flex-end; margin-top: 8px">
        <button class="mini" :disabled="resvLoading" @click="loadReservations">{{ resvLoading ? "…" : t("admin.refresh") }}</button>
      </div>
      <table class="utable">
        <thead>
          <tr><th>{{ t("admin.colDevice") }}</th><th>{{ t("admin.colHolder") }}</th><th>{{ t("admin.colExpires") }}</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="r in reservations" :key="r.device_id">
            <td class="dev-cell" :title="r.device_id">{{ deviceLabel(r.device_id) }}</td>
            <td>{{ r.username }}</td>
            <td class="muted">{{ fmtExpires(r.expires_at) }}</td>
            <td><button class="mini danger" :disabled="busyDev === r.device_id" @click="forceRelease(r)">{{ busyDev === r.device_id ? "…" : t("admin.forceRelease") }}</button></td>
          </tr>
          <tr v-if="!reservations.length">
            <td colspan="4" class="muted" style="text-align: center; padding: 14px">{{ t("admin.noReservations") }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { authApi } from "../api/auth";
import { reservationsApi } from "../api/reservations";
import { useAuth } from "../composables/useAuth";
import { useUiI18n } from "../composables/useUiI18n";
import { useConfirm } from "../composables/useConfirm";
import { useToast } from "../composables/useToast";
import { useDevices } from "../composables/useDevices";
import SelectMenu from "./SelectMenu.vue";

defineEmits(["close"]);
const { t } = useUiI18n();
const { state: authState } = useAuth();

// Actor (the logged-in admin/super_admin) drives what's editable. Mirrors the
// backend _can_manage: super_admin re-ranks anyone (except itself / other
// super_admins via the UI — superadmin stays the singular fallback); a plain
// admin only manages user accounts (no role control, can't delete admins).
const meId = computed(() => authState.user?.id);
const isSuperAdmin = computed(() => authState.user?.role === "super_admin");
const rankOptions = computed(() => [
  { value: "user", label: t("admin.roleUser") },
  { value: "admin", label: t("admin.roleAdmin") },
]);
const canEditRole = (u) =>
  isSuperAdmin.value && u.id !== meId.value && u.role !== "super_admin";
const canDelete = (u) => u.id !== meId.value && u.role !== "super_admin";
const { ask } = useConfirm();
const { push: toast } = useToast();
const { devices, refresh: refreshDevices } = useDevices();

const tab = ref("users");
const users = ref([]);
const error = ref("");
const nu = reactive({ username: "", email: "", password: "", role: "user" });
const roleOptions = computed(() => [
  { value: "user", label: t("admin.roleUser") },
  { value: "admin", label: t("admin.roleAdmin") },
]);

const reservations = ref([]);
const resvLoading = ref(false);
const busyDev = ref("");

// Distinguishing label — name alone is ambiguous (every iPhone is "iPhone").
// Show the model + the FULL UDID; the cell shows it whole where there's room and
// only ellipsizes (via CSS) when cramped, with the full value on hover.
function deviceLabel(id) {
  const d = devices.value.find((x) => x.udid === id);
  const model = d?.product_type || d?.name;
  return model ? `${model} · UDID ${id}` : `UDID ${id}`;
}
function fmtExpires(ts) {
  if (!ts) return "—";
  // backend sends "YYYY-MM-DD HH:MM:SS[.ffffff]" (server-local). Show date + HH:MM
  // — a bare time read as "00:00" looked wrong when the hold crossed midnight.
  const m = String(ts).match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : String(ts);
}

async function refresh() {
  error.value = "";
  try {
    users.value = (await authApi.listUsers()).users;
  } catch (e) {
    error.value = e.message;
  }
}
async function loadReservations() {
  resvLoading.value = true;
  error.value = "";
  try {
    // Pull the device list too so reservations can show model + UDID tail.
    refreshDevices().catch(() => {});
    reservations.value = (await reservationsApi.list()).reservations || [];
  } catch (e) {
    error.value = e.message;
  } finally {
    resvLoading.value = false;
  }
}
function switchToReservations() {
  tab.value = "reservations";
  loadReservations();
}

async function forceRelease(r) {
  if (!(await ask(t("admin.confirmForceRelease", { device: deviceLabel(r.device_id), user: r.username })))) return;
  busyDev.value = r.device_id;
  error.value = "";
  try {
    await reservationsApi.release(r.device_id); // admin force-release on the backend
    toast(t("admin.released"));
    await loadReservations();
  } catch (e) {
    error.value = e.message;
  } finally {
    busyDev.value = "";
  }
}

async function create() {
  error.value = "";
  try {
    await authApi.createUser({ ...nu });
    nu.username = nu.email = nu.password = "";
    await refresh();
  } catch (e) {
    error.value = e.message;
  }
}
async function toggleActive(u) {
  try {
    await authApi.updateUser(u.id, { is_active: !u.is_active });
    await refresh();
  } catch (e) {
    error.value = e.message;
  }
}
async function changeRole(u, role) {
  if (role === u.role) return;
  error.value = "";
  try {
    await authApi.updateUser(u.id, { role });
    toast(t("admin.roleChanged", { name: u.username, role: t("admin.role" + (role === "admin" ? "Admin" : "User")) }));
    await refresh();
  } catch (e) {
    error.value = e.message;
    await refresh(); // revert the dropdown to the server's truth
  }
}
async function remove(u) {
  if (!(await ask(t("admin.confirmDelete", { name: u.username })))) return;
  try {
    await authApi.deleteUser(u.id);
    await refresh();
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(refresh);
</script>

<style scoped>
.tabs { display: flex; gap: 2px; margin-top: 10px; border-bottom: 1px solid var(--border); }
.tab {
  min-height: 36px; flex: 0 0 auto; background: transparent; border: none; border-radius: 8px 8px 0 0;
  color: var(--muted); padding: 6px 14px; font-weight: 500; box-shadow: inset 0 -2px 0 transparent;
}
.tab:hover:not(.active) { color: var(--text); background: transparent; }
.tab.active { color: var(--text); font-weight: 650; box-shadow: inset 0 -2px 0 var(--primary); }
.utable { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.78rem; }
.utable th, .utable td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--border); }
/* Device cell: show the full UDID where it fits, ellipsize only when cramped. */
.dev-cell { max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.mini { padding: 2px 8px; font-size: 0.72rem; }
.block { margin-top: 12px; }
.create-box {
  margin-top: 12px; padding: 12px; border: 1px solid var(--border);
  border-radius: 10px; background: var(--surface-2);
}
</style>
