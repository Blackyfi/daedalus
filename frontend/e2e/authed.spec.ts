import { test, expect } from "@playwright/test";

// Authed E2E — runs ONLY against the 3FA-bypass test stack (`make test.up`).
// It skips itself automatically on any stack that does not advertise the
// bypass, so it can never fail the normal (secret-free) smoke gate.
//
// Serial: the steps share one backend DB and build on each other (empty-state →
// create → navigate), so parallel workers would race on global project state.
test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ request }) => {
  const body = await (await request.get("/api/v1/auth/status")).json();
  test.skip(!body.test_bypass, "requires the 3FA-bypass test stack (make test.up)");
});

test("test-login button signs in and lands on the app shell", async ({ page }) => {
  await page.goto("/login");
  const btn = page.getByTestId("test-login");
  await expect(btn).toBeVisible();
  await btn.click();
  // Redirects to "/" → the Projects page heading renders inside the shell.
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
});

test("onboarding empty-state is shown on a fresh stack", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("test-login").click();
  await expect(page.getByText(/Welcome to Daedalus/i)).toBeVisible();
  await expect(page.getByText(/Discover repos/i).first()).toBeVisible();
});

test("can create a project and see it listed", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("test-login").click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();

  // The form labels aren't associated to their inputs, so scope by the panel.
  const np = page.locator("section.panel", { hasText: "New project" });
  const name = "E2E Smoke Project";
  await np.locator("input.field").first().fill(name);
  await np.locator('input[placeholder="/workspaces/my-repo"]').fill("/workspaces/e2e-smoke");
  await np.getByRole("button", { name: "Create", exact: true }).click();

  // The card list re-renders with the new project; the empty-state disappears.
  await expect(page.getByText(name)).toBeVisible();
});

test("primary pages render under auth without bouncing to login", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("test-login").click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();

  for (const path of ["/kpis", "/connectors", "/audit", "/security", "/algorithms"]) {
    await page.goto(path);
    // Authed routes must keep the URL (no redirect to /login) and render the
    // persistent shell nav — proof the page mounted without crashing.
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.locator("nav").first()).toBeVisible();
  }
});
