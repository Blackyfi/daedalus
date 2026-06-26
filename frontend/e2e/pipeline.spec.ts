import { test, expect } from "@playwright/test";

// Run-pipeline UI flows (#9 ship-undo, #10 plan steering), driven in a real
// browser against the 3FA-bypass test stack. The backend state these need (a
// committed run -> awaiting_review batch; a pending plan) is provisioned out of
// band by `make test.e2e.pipeline`, which injects MERGE_PID / PLAN_PID. The
// tests skip when that wiring is absent so they never break the secret-free gate.
const MERGE_PID = process.env.MERGE_PID;
const PLAN_PID = process.env.PLAN_PID;

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ request }) => {
  const body = await (await request.get("/api/v1/auth/status")).json();
  test.skip(!body.test_bypass, "requires the 3FA-bypass test stack");
  test.skip(!MERGE_PID || !PLAN_PID, "requires MERGE_PID/PLAN_PID (make test.e2e.pipeline)");
});

async function login(page) {
  await page.goto("/login");
  await page.getByTestId("test-login").click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
}

test("#10 plan-review steering saves guidance and re-plans", async ({ page }) => {
  await login(page);
  await page.goto(`/projects/${PLAN_PID}`);
  await expect(page.getByText(/Plan Review/i)).toBeVisible();

  await page
    .getByPlaceholder(/split the auth work/i)
    .fill("Prefer many small tasks; keep docs and tests separate.");
  await page.getByRole("button", { name: /Save guidance & re-plan/i }).click();

  // The mutation saves a project note, discards the draft, and re-plans.
  await expect(page.getByText(/Saved guidance/i)).toBeVisible();
});

test("#9 ship then undo from the merge modal", async ({ page }) => {
  await login(page);
  await page.goto(`/projects/${MERGE_PID}`);

  // Open the awaiting-review batch from the action bar.
  await page.getByText(/ready to ship/i).first().click();

  // Modal opens at the Ship step → ship it.
  await page.getByRole("button", { name: /^Ship to / }).click();

  // The fix: after shipping, the batch stays `shipped`, so the Undo affordance
  // appears (and persists) instead of the status poll reverting it.
  const undo = page.getByRole("button", { name: /Undo ship/i });
  await expect(undo).toBeVisible();
  await undo.click();
  await expect(page.getByText(/Ship undone/i)).toBeVisible();
});
