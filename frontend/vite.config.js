import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the React server proxies API/auth calls to Flask on :5050.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:5050", "/auth": "http://127.0.0.1:5050" },
  },
});
