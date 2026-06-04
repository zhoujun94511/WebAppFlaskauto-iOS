import { http } from "./http";

const base = (udid) => `/api/devices/${encodeURIComponent(udid)}`;

export const automationApi = {
  find: (udid, using, value) => http.post(`${base(udid)}/element/find`, { using, value }),
  tap: (udid, using, value) => http.post(`${base(udid)}/element/tap`, { using, value }),
  type: (udid, using, value, text, clear = false) =>
    http.post(`${base(udid)}/element/type`, { using, value, text, clear }),
  pinch: (udid, using, value, scale, velocity = 1.0) =>
    http.post(`${base(udid)}/element/pinch`, { using, value, scale, velocity }),
  foreground: (udid) => http.get(`${base(udid)}/foreground`),
  source: (udid) => http.get(`${base(udid)}/source`),
};
