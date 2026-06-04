import { http } from "./http";

export const reservationsApi = {
  list: () => http.get("/api/reservations"),
  claim: (device_id, minutes) => http.post("/api/reservations", { device_id, minutes }),
  release: (device_id) => http.del(`/api/reservations/${encodeURIComponent(device_id)}`),
};
