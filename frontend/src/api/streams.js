import { http } from "./http";

const base = (udid) => `/api/devices/${encodeURIComponent(udid)}`;

export const streamsApi = {
  start: (udid, provider) => http.post(`${base(udid)}/stream/start`, { provider }),
  stop: (udid) => http.post(`${base(udid)}/stream/stop`),
  status: (udid) => http.get(`${base(udid)}/stream/status`),
};
