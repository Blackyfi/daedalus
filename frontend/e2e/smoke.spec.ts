import { test, expect } from "@playwright/test";

// Unauthenticated smoke checks against the live stack — no secrets required.
// The full 3FA + task-run E2E is documented in docs/QUALITY_PLAN.md and is a
// follow-up (it needs the throwaway-user + OTP-injection helper).

test("API health is OK", async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.status()).toBe(200);
  expect(await res.json()).toMatchObject({ status: "ok" });
});

test("auth status is unauthenticated without a session", async ({ request }) => {
  const res = await request.get("/api/v1/auth/status");
  expect(res.status()).toBe(200);
  expect(await res.json()).toMatchObject({ authenticated: false });
});

test("SPA serves the login form", async ({ page }) => {
  await page.goto("/");
  // The password-step form renders first: a Password field + Continue button.
  await expect(page.getByText("Password", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /continue/i })).toBeVisible();
});
