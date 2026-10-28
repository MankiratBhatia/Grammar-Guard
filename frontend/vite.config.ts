import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the Vite server proxies /api to the FastAPI backend (python -m grammarguard.api).
// In production `npm run build` writes frontend/dist, which FastAPI serves itself (one process, one port).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { sourcemap: true, chunkSizeWarningLimit: 1500 /* Graphviz-as-WebAssembly, loaded lazily */ },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
