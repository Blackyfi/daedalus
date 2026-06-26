import { defineConfig, devices } from "@playwright/test";

// E2E runs against a LIVE Daedalus stack (Caddy on :9443 by default).
// Override with E2E_BASE_URL. Not part of the default unit/component gate —
// run with `npm run test:e2e`. The full authed 3FA flow needs the throwaway-
// user + OTP-injection recipe (see docs/QUALITY_PLAN.md §Test environment);
// this smoke layer covers the unauthenticated surface that needs no secrets.
const baseURL = process.env.E2E_BASE_URL ?? "https://localhost:9443";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    ignoreHTTPSErrors: true, // Caddy serves an internal-CA cert
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], channel: undefined },
    },
  ],
});
