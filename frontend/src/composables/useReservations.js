import { reactive } from "vue";
import { reservationsApi } from "../api/reservations";
import { useSocket } from "./useSocket";
import { useAuth } from "./useAuth";

// Shared reservation state (module singleton): device_id → reservation.
const state = reactive({
  byDevice: {},
  maxMinutes: 240,
  defaultMinutes: 60,
  loaded: false,
});
let _bound = false;
let _refetchTimer = null;

export function useReservations() {
  const { on } = useSocket();

  async function fetchReservations() {
    try {
      const d = await reservationsApi.list();
      const map = {};
      for (const r of d.reservations || []) map[r.device_id] = r;
      state.byDevice = map;
      state.maxMinutes = d.max_minutes ?? state.maxMinutes;
      state.defaultMinutes = d.default_minutes ?? state.defaultMinutes;
      state.loaded = true;
    } catch {
      /* not logged in / auth off — leave empty */
    }
  }

  function _debouncedRefetch() {
    clearTimeout(_refetchTimer);
    _refetchTimer = setTimeout(fetchReservations, 120);
  }

  async function claim(deviceId, minutes) {
    await reservationsApi.claim(deviceId, minutes || state.defaultMinutes);
    await fetchReservations();
  }
  async function release(deviceId) {
    await reservationsApi.release(deviceId);
    await fetchReservations();
  }

  // Live updates: any claim/release anywhere → refetch.
  if (!_bound) {
    _bound = true;
    on("reservation_changed", _debouncedRefetch);
    on("device_released", _debouncedRefetch);
  }

  const get = (deviceId) => state.byDevice[deviceId] || null;

  // Whether the current user may control (stream/input/files/apps) a device —
  // mirrors the backend reservations.assert_owner: admins/super_admins bypass
  // occupation entirely; a normal user must hold THIS device's reservation.
  // An unreserved device is NOT free-for-all for normal users — they must claim
  // it first (anti-abuse gate).
  function canControl(deviceId) {
    const { isAdmin } = useAuth();
    if (isAdmin.value) return true;
    return !!get(deviceId)?.is_mine;
  }

  return { state, fetchReservations, claim, release, get, canControl };
}
