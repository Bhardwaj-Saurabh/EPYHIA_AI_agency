import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Dev: the local gateway (8080) proxies /admin/api to Tier 2 -> Tier 3.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { "/admin/api": "http://localhost:8080" },
  },
});
