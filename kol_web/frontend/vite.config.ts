import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/static/kol/",
  plugins: [react()],
  build: {
    outDir: "../../static/kol",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/kol-api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/kol-api/, "/api"),
      },
    },
  },
});
