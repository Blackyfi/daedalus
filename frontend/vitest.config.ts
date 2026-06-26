import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Component + unit tests run in jsdom and need no backend.
// E2E (Playwright) lives under e2e/ and is excluded here — run it via
// `npm run test:e2e` against a live stack.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**"],
  },
});
