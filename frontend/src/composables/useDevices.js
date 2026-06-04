import { ref } from "vue";
import { devicesApi } from "../api/devices";
import { useSocket } from "./useSocket";

const devices = ref([]);
const loading = ref(false);
const error = ref("");
const backendHealthy = ref(null); // pymobiledevice3 available (only valid when reachable)
const backendReachable = ref(null); // did /api/health answer at all?
const webrtcEnabled = ref(false);
let _bound = false;

export function useDevices() {
  const { on, emit } = useSocket();

  if (!_bound) {
    _bound = true;
    on("devices:changed", (data) => {
      if (data && Array.isArray(data.devices)) devices.value = data.devices;
    });
  }

  async function refresh(rescan = true) {
    loading.value = true;
    error.value = "";
    try {
      const data = await devicesApi.list(rescan);
      devices.value = data.devices || [];
    } catch (e) {
      error.value = e.message || "Failed to list devices";
    } finally {
      loading.value = false;
    }
  }

  async function checkHealth() {
    try {
      const h = await devicesApi.health();
      backendReachable.value = true;
      // Only meaningful when the backend actually answered.
      backendHealthy.value = !!h.pymobiledevice3_available;
      webrtcEnabled.value = !!h.webrtc_enabled;
      return h;
    } catch {
      // Request failed → the backend is unreachable (not "pymobiledevice3
      // missing"). Leave backendHealthy unknown so we don't show the wrong banner.
      backendReachable.value = false;
      backendHealthy.value = null;
      return null;
    }
  }

  async function connect(udid) {
    return devicesApi.connect(udid).then((d) => {
      _replace(d.device);
      return d.device;
    });
  }

  async function disconnect(udid) {
    await devicesApi.disconnect(udid);
    const d = devices.value.find((x) => x.udid === udid);
    if (d) d.connected = false;
  }

  function _replace(device) {
    const i = devices.value.findIndex((x) => x.udid === device.udid);
    if (i >= 0) devices.value[i] = device;
    else devices.value.push(device);
  }

  return { devices, loading, error, backendHealthy, backendReachable, webrtcEnabled, refresh, checkHealth, connect, disconnect, emit };
}
