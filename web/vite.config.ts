import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const API_TARGET = process.env.VITE_API_BASE ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    host: "0.0.0.0",
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/webhook": { target: API_TARGET, changeOrigin: true },
      "/auth/gitlab": { target: API_TARGET, changeOrigin: true },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
