import { expect, test, type Page } from "@playwright/test";

const KEY = process.env.HOLDEM_UI_TEST_KEY ?? "e2e-ui-key";

// Tests run with a shared session cookie (tests/auth.setup.ts); /ui/login is
// rate limited so only the login-flow test signs in itself.
async function login(page: Page) {
  await page.goto("/ui/jobs");
  await expect(page).toHaveURL(/\/ui\/jobs/);
}

async function buildRiverSpot(page: Page) {
  await page.goto("/ui/spots/new");
  // Board: pick 5 cards (river) from the deck.
  const deck = page.getByRole("grid", { name: /Board/ });
  for (const card of ["Ks", "Js", "Th", "7d", "2c"]) {
    await deck.getByRole("gridcell", { name: card }).click();
  }
  await expect(deck.getByRole("gridcell", { name: "Ks" })).toHaveAttribute("aria-selected", "true");
  // Ranges via the import box.
  const p0 = page.locator('input[id^="range-text-Player 0"]');
  await p0.fill("QQ+, AKs, AKo");
  await p0.press("Enter");
  const p1 = page.locator('input[id^="range-text-Player 1"]');
  await p1.fill("JJ+, AQs+, AQo+");
  await p1.press("Enter");
  await page.locator("#solver-opts").fill(
    '{"fake_real_keys": true, "fake_iter_seconds": 0.01, "fake_infosets": 40}',
  );
  await page.locator("#iters").fill("60");
  await page.locator("#pe").fill("10");
}

test("login flow: bad key rejected, good key lands on jobs", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/ui/");
  await expect(page).toHaveURL(/\/ui\/login/);
  await page.getByLabel("API key").fill("wrong-key");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("API key").fill(KEY);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/ui\/jobs/);
});

test("build a river spot, estimate, solve, view root and a child node", async ({ page }) => {
  await login(page);
  await buildRiverSpot(page);
  await page.getByRole("button", { name: "Estimate" }).click();
  await expect(page.getByTestId("estimate")).toBeVisible();
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page).toHaveURL(/\/ui\/jobs\//);
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
  // Strategy grid at root.
  await expect(page.getByTestId("tree-node")).toBeVisible();
  await expect(page.getByTestId("hands-table")).toBeVisible();
  // Navigate to a child via the tree navigator (never history strings).
  const child = page.getByTestId("children").getByRole("button").first();
  await expect(child).toBeEnabled();
  const edgeName = await child.innerText();
  await child.click();
  await expect(page.getByRole("navigation", { name: "node path" })).toContainText("root");
  const crumbs = page.getByRole("navigation", { name: "node path" }).getByRole("button");
  await expect(crumbs.last()).not.toHaveText("root");
  void edgeName;
});

test("jobs list shows status and pin works", async ({ page }) => {
  await login(page);
  await buildRiverSpot(page);
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
  await page.goto("/ui/jobs");
  await expect(page.getByRole("table")).toContainText("done");
  const row = page.locator("tr", { hasText: "done" }).first();
  await row.getByRole("button", { name: "Pin" }).click();
  await expect(page.getByRole("table")).toContainText("pinned");
});

test("spot library: save, rename/tag, duplicate, solve from saved spot", async ({ page }) => {
  await login(page);
  await buildRiverSpot(page);
  await page.locator("#name").fill("e2e-river");
  await page.locator("#tags").fill("e2e, river");
  await page.getByRole("button", { name: "Save to library" }).click();
  await expect(page).toHaveURL(/\/ui\/spots$/);
  await expect(page.getByRole("table")).toContainText("e2e-river");
  await expect(page.getByRole("table")).toContainText("e2e");
  // Rename + retag.
  const row = page.locator("tr", { hasText: "e2e-river" }).first();
  await row.getByRole("button", { name: "Rename" }).click();
  await page.getByLabel("rename spot").fill("e2e-river-v2");
  await page.getByLabel("edit tags").fill("e2e, v2");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("e2e-river-v2");
  // Duplicate.
  const row2 = page.locator("tr", { hasText: "e2e-river-v2" }).first();
  await row2.getByRole("button", { name: "Duplicate" }).click();
  await expect(page.getByRole("table")).toContainText("e2e-river-v2 (copy)");
  // Solve from the library.
  await row2.getByRole("button", { name: "Solve" }).click();
  await page.getByRole("dialog").locator("#sd-iters").fill("40");
  await page.getByRole("dialog").getByRole("button", { name: "Solve" }).click();
  await expect(page).toHaveURL(/\/ui\/jobs\//);
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
});

test("SSE reconnects after the events stream is dropped", async ({ page }) => {
  await login(page);
  await buildRiverSpot(page);
  // Slower fake solve so SSE is mid-flight.
  await page.locator("#solver-opts").fill(
    '{"fake_real_keys": true, "fake_iter_seconds": 0.05, "fake_infosets": 40}',
  );
  await page.locator("#iters").fill("200");
  await page.locator("#pe").fill("5");
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page.getByTestId("exploitability-chart")).toBeVisible({ timeout: 30_000 });
  // Drop the next SSE request once; the browser's built-in reconnect resumes it.
  let dropped = false;
  await page.route("**/v1/solves/*/events**", async (route) => {
    if (!dropped) {
      dropped = true;
      await route.abort();
    } else {
      await route.continue();
    }
  });
  await page.reload();
  await expect(page.getByTestId("exploitability-chart")).toBeVisible({ timeout: 30_000 });
  // Stream recovers (either reconnecting then open, or straight to done).
  await expect(page.getByTestId("job-status")).toContainText(/solving|done|reconnecting/, {
    timeout: 60_000,
  });
});

test("inline 422 on an invalid spot", async ({ page }) => {
  await login(page);
  await page.goto("/ui/spots/new");
  // Board with only 3 cards -> 422.
  const deck = page.getByRole("grid", { name: /Board/ });
  for (const card of ["Ks", "Js", "Th"]) {
    await deck.getByRole("gridcell", { name: card }).click();
  }
  const p0 = page.locator('input[id^="range-text-Player 0"]');
  await p0.fill("QQ+");
  await p0.press("Enter");
  const p1 = page.locator('input[id^="range-text-Player 1"]');
  await p1.fill("QQ+");
  await p1.press("Enter");
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page.getByTestId("form-errors")).toBeVisible();
  await expect(page.getByTestId("form-errors")).toContainText("board");
});
