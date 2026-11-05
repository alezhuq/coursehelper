/// <reference types="vitest/config" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

// Env-driven so the same config works unchanged for host `npm run dev`
// (defaults below) and inside Docker Compose's dev override, which sets
// these to reach the `api` service and cope with the Docker Desktop for
// macOS bind-mount filesystem-events gap.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";
const usesPolling = process.env.VITE_USE_POLLING === "true";
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT
  ? Number(process.env.VITE_HMR_CLIENT_PORT)
  : undefined;

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(currentDir, "./src"),
    },
  },
  server: {
    host: true, // bind 0.0.0.0 so the port mapping reaches it from the host
    watch: usesPolling ? { usePolling: true } : undefined,
    hmr: hmrClientPort ? { clientPort: hmrClientPort } : undefined,
    proxy: {
      // Mirrors nginx's production /api/ reverse proxy so the same
      // fetch('/api/health') call works identically in `npm run dev` and
      // behind nginx in Docker Compose.
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        // nginx's `location /api/ { proxy_pass http://api:8000/; }` strips
        // the /api prefix before forwarding; mirror that here so the same
        // fetch('/api/health') call resolves to the api's real /health path.
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    // Only collect unit specs under src/. Without this, vitest's default glob
    // also matches the Playwright specs in e2e/, which then crash with
    // "Playwright Test did not expect test.describe() to be called here" -
    // the two runners are incompatible and vitest must never collect e2e.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    environment: "jsdom",
    // No unit tests exist yet; succeed rather than failing CI with "no test
    // files found" until the first src/ spec lands.
    passWithNoTests: true,
  },
});
