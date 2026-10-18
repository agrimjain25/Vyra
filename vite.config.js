import { defineConfig } from "vite";

// Optional dev server (npm run dev). The backend must be running on port 8000.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/media": "http://127.0.0.1:8000",
    },
  },
});
