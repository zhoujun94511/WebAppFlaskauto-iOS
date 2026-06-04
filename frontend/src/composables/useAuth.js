import { computed, reactive } from "vue";
import { authApi } from "../api/auth";
import { useSocket } from "./useSocket";

// Shared auth state (module singleton).
const state = reactive({
  ready: false, // has the initial auth check completed?
  user: null, // { id, username, email, role } or null
});

const isAdmin = computed(() => ["admin", "super_admin"].includes(state.user?.role));

export function useAuth() {
  async function checkAuth() {
    try {
      const d = await authApi.checkAuth();
      state.user = d.authenticated ? d.user : null;
    } catch {
      state.user = null;
    } finally {
      state.ready = true;
    }
    return state.user;
  }

  async function login(username, password) {
    const d = await authApi.login(username, password); // throws on failure
    state.user = d.user;
    // Re-handshake the socket so it carries the freshly-set session cookie;
    // otherwise the boot-time anonymous socket stays unauthenticated and
    // stream/control over Socket.IO fails until a manual page refresh.
    useSocket().reconnect();
    return state.user;
  }

  async function register(username, email, password) {
    return authApi.register(username, email, password); // {message}
  }

  async function logout() {
    try {
      await authApi.logout();
    } catch {
      /* ignore */
    }
    state.user = null;
    useSocket().reconnect(); // drop the authenticated socket back to anonymous
  }

  return { state, isAdmin, checkAuth, login, register, logout };
}
