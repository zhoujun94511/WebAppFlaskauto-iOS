import { http } from "./http";

const base = (udid) => `/api/devices/${encodeURIComponent(udid)}`;

export const controlApi = {
  tap: (udid, payload) => http.post(`${base(udid)}/tap`, payload),
  swipe: (udid, payload) => http.post(`${base(udid)}/swipe`, payload),
  longPress: (udid, payload) => http.post(`${base(udid)}/longpress`, payload),
  doubleTap: (udid, payload) => http.post(`${base(udid)}/doubletap`, payload),
  getAlert: (udid) => http.get(`${base(udid)}/alert`),
  alertAction: (udid, action) => http.post(`${base(udid)}/alert`, { action }),
  input: (udid, text) => http.post(`${base(udid)}/input`, { text }),
  button: (udid, name) => http.post(`${base(udid)}/button`, { name }),
  key: (udid, name) => http.post(`${base(udid)}/key`, { name }),
  accessibility: (udid, feature, action = "toggle") =>
    http.post(`${base(udid)}/accessibility`, { feature, action }),
  getClipboard: (udid) => http.get(`${base(udid)}/clipboard`),
  setClipboard: (udid, text) => http.post(`${base(udid)}/clipboard`, { text }),
  screenshot: (udid) => http.post(`${base(udid)}/screenshot`),
  launch: (udid, bundle_id) => http.post(`${base(udid)}/launch`, { bundle_id }),
  terminate: (udid, bundle_id) =>
    http.post(`${base(udid)}/terminate`, { bundle_id }),
};
