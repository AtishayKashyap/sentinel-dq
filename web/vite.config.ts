/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // In dev the API runs on :8000; proxying avoids CORS entirely.
    proxy: { "/api": "http://localhost:8000" },
  },
  test: { environment: "jsdom", globals: false },
});
