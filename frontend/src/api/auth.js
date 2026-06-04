import { http } from "./http";

export const authApi = {
  checkAuth: () => http.get("/api/auth/check-auth"),
  login: (username, password) => http.post("/api/auth/login", { username, password }),
  register: (username, email, password) =>
    http.post("/api/auth/register", { username, email, password }),
  logout: () => http.post("/api/auth/logout"),
  changePassword: (old_password, new_password) =>
    http.post("/api/auth/change-password", { old_password, new_password }),
  // user management (admin)
  listUsers: () => http.get("/api/auth/users"),
  createUser: (payload) => http.post("/api/auth/users", payload),
  updateUser: (id, payload) => http.put(`/api/auth/users/${id}`, payload),
  deleteUser: (id) => http.del(`/api/auth/users/${id}`),
};
