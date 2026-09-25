import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  cacheDir: ".vite-cache",
  build: { rollupOptions: { output: { inlineDynamicImports: true } } },
  server: { proxy: { "/api": "http://127.0.0.1:4320" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["node_modules/**", "dist/**"],
  },
});
