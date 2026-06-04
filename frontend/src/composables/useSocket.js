import { io } from "socket.io-client";
import { ref } from "vue";

// One shared Socket.IO connection. Force long-polling first to match the
// backend (Flask-SocketIO threading mode can't always upgrade to WS cleanly),
// then let it upgrade if possible.
let _socket = null;
const connected = ref(false);

export function useSocket() {
  if (!_socket) {
    _socket = io({
      path: "/socket.io",
      transports: ["polling", "websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
    });
    _socket.on("connect", () => (connected.value = true));
    _socket.on("disconnect", () => (connected.value = false));
  }

  function on(event, handler) {
    _socket.on(event, handler);
    return () => _socket.off(event, handler);
  }

  function emit(event, payload) {
    _socket.emit(event, payload);
  }

  // Force a fresh handshake so the server re-reads the session cookie. The
  // socket connects once at app boot (while logged out); Flask-SocketIO freezes
  // that anonymous session for the connection's life, so every socket-gated
  // feature (WebRTC/stream owner-check) would stay "未登录" until reconnect.
  // Call this right after login/logout. Buffered emits flush after reconnect,
  // and on()-registered handlers persist across it.
  function reconnect() {
    if (!_socket) return;
    _socket.disconnect();
    _socket.connect();
  }

  return { socket: _socket, connected, on, emit, reconnect };
}
