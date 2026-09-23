import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Dev server proxies API + Socket.IO to the Flask backend on :5001, so the
// frontend code can use same-origin relative paths in both dev and prod
// (prod is served by Flask itself from dist/).
export default defineConfig({
  // comments: false strips template `<!-- ... -->` notes from the rendered DOM
  // (Vue keeps them as comment nodes in dev by default — they show up in F12).
  // Source comments are untouched; only browser output is cleaned, dev + prod.
  plugins: [vue({ template: { compilerOptions: { comments: false } } })],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:5001", changeOrigin: true },
      "/socket.io": {
        target: "http://127.0.0.1:5001",
        ws: true,
        changeOrigin: true,
        configure(proxy) {
          // Vite logs every proxy-socket error in red. A client disconnect
          // (refresh, login reconnect) aborts the upstream write; that is the
          // same close as the backend, not a failed API call. Keep
          // ECONNREFUSED and anything else visible.
          proxy.on("proxyReqWs", (_proxyReq, _req, socket) => {
            const emit = socket.emit.bind(socket);
            socket.emit = (event, ...args) => {
              const code = args[0] && args[0].code;
              if (
                event === "error" &&
                (code === "ECONNABORTED" || code === "ECONNRESET" || code === "EPIPE")
              ) {
                return false;
              }
              return emit(event, ...args);
            };
          });
        },
      },
    },
  },
  build: { outDir: "dist" },
});
