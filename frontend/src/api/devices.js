import { http } from "./http";

export const devicesApi = {
  list: (rescan = true) => http.get(`/api/devices?rescan=${rescan ? 1 : 0}`),
  get: (udid) => http.get(`/api/devices/${encodeURIComponent(udid)}`),
  info: (udid) => http.get(`/api/devices/${encodeURIComponent(udid)}/info`),
  streamQuality: (udid, scaling) =>
    http.post(`/api/devices/${encodeURIComponent(udid)}/stream/quality`, { scaling }),
  connect: (udid) => http.post(`/api/devices/${encodeURIComponent(udid)}/connect`),
  disconnect: (udid) =>
    http.post(`/api/devices/${encodeURIComponent(udid)}/disconnect`),
  wdaStatus: (udid) =>
    http.post(`/api/devices/${encodeURIComponent(udid)}/wda/status`),
  health: () => http.get(`/api/health`),
};
