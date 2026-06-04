import { http } from "./http";

const base = (udid) => `/api/devices/${encodeURIComponent(udid)}`;

export const appsApi = {
  list: (udid) => http.get(`${base(udid)}/apps`),
  uninstall: (udid, bundle_id) =>
    http.post(`${base(udid)}/apps/uninstall`, { bundle_id }),
  install: (udid, file) => {
    const fd = new FormData();
    fd.append("ipa", file);
    return http.postForm(`${base(udid)}/apps/install`, fd);
  },
};
