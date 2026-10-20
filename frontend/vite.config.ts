import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package so `pip install vizcontrol` ships the viewer (no Node needed).
// `npm run dev` proxies the API to a running `vizcontrol serve`.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../src/vizcontrol/viewer/static", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  server: { proxy: { "/api": "http://127.0.0.1:7171" } },
});
