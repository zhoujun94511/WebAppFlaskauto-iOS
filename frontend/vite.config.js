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
      },
    },
  },
  build: { outDir: "dist" },
});
