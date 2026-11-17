import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/ui/",
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
    },
  },
});
