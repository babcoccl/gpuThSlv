import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Shared session cookie from tests/auth.setup.ts.
async function login(page: Page) {
  await page.goto("/ui/jobs");
  await expect(page).toHaveURL(/\/ui\/jobs/);
}

async function buildRiverSpot(page: Page) {
  await page.goto("/ui/spots/new");
  const deck = page.getByRole("grid", { name: /Board/ });
  for (const card of ["Ks", "Js", "Th", "7d", "2c"]) {
    await deck.getByRole("gridcell", { name: card }).click();
  }
  await page.locator('input[id^="range-text-Player 0"]').fill("QQ+, AKs, AKo");
  await page.locator('input[id^="range-text-Player 0"]').press("Enter");
  await page.locator('input[id^="range-text-Player 1"]').fill("JJ+, AQs+, AQo+");
  await page.locator('input[id^="range-text-Player 1"]').press("Enter");
  await page.locator("#solver-opts").fill(
    '{"fake_real_keys": true, "fake_iter_seconds": 0.01, "fake_infosets": 40}',
  );
  await page.locator("#iters").fill("60");
  await page.locator("#pe").fill("10");
}

async function expectNoSerious(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target).join(", ")}`), `${name} has serious/critical axe violations`).toEqual([]);
}

test("axe: no serious/critical violations on any screen", async ({ page }) => {
  await page.goto("/ui/login");
  await expectNoSerious(page, "login");
  await login(page);
  await page.goto("/ui/jobs");
  await expectNoSerious(page, "jobs");
  await buildRiverSpot(page);
  await expectNoSerious(page, "spot setup");
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
  await expect(page.getByTestId("tree-node")).toBeVisible();
  await expectNoSerious(page, "strategy viewer");
  await page.goto("/ui/spots");
  await expectNoSerious(page, "spot library");
});

test("keyboard-only: setup form, deck arrows, range import, solve", async ({ page }) => {
  await login(page);
  await page.goto("/ui/spots/new");

  // Deck: focus a cell, arrow to each target card, Space selects.
  const deck = page.getByRole("grid", { name: /Board/ });
  await deck.getByRole("gridcell", { name: "As" }).focus();
  const cards = ["Ks", "Js", "Th", "7d", "2c"];
  for (const card of cards) {
    // Deck grid is 13 columns wide: Down/Up move a displayed row (±13),
    // Right/Left a cell (±1). Walk row-first then column.
    let guard = 0;
    while ((await page.evaluate(() => document.activeElement?.getAttribute("data-card"))) !== card) {
      const cur = await page.evaluate(() => document.activeElement?.getAttribute("data-card"));
      const ALL = "AKQJT98765432".split("").flatMap((r) => "sdch".split("").map((s) => r + s));
      const i = ALL.indexOf(cur!), j = ALL.indexOf(card);
      const rowDelta = Math.floor(j / 13) - Math.floor(i / 13);
      const colDelta = (j % 13) - (i % 13);
      if (rowDelta > 0) await page.keyboard.press("ArrowDown");
      else if (rowDelta < 0) await page.keyboard.press("ArrowUp");
      else if (colDelta > 0) await page.keyboard.press("ArrowRight");
      else await page.keyboard.press("ArrowLeft");
      if (++guard > 60) throw new Error(`could not arrow to ${card}`);
    }
    await page.keyboard.press(" ");
  }
  await expect(deck.getByRole("gridcell", { name: "Ks" })).toHaveAttribute("aria-selected", "true");

  // Ranges: focus text boxes, type, Enter imports.
  const p0 = page.locator('input[id^="range-text-Player 0"]');
  await p0.focus();
  await page.keyboard.type("QQ+, AKs, AKo");
  await page.keyboard.press("Enter");
  const p1 = page.locator('input[id^="range-text-Player 1"]');
  await p1.focus();
  await page.keyboard.type("JJ+, AQs+, AQo+");
  await page.keyboard.press("Enter");

  const typeReplace = async (sel: string, text: string) => {
    await page.locator(sel).focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type(text);
  };
  await typeReplace("#solver-opts", '{"fake_real_keys": true, "fake_iter_seconds": 0.01, "fake_infosets": 40}');
  await typeReplace("#iters", "60");
  await typeReplace("#pe", "10");

  const solve = page.getByRole("button", { name: "Solve", exact: true });
  await solve.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/ui\/jobs\//);
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
});

test("keyboard-only: viewer node navigation and spot library", async ({ page }) => {
  await login(page);
  await page.goto("/ui/spots/new");
  const deck = page.getByRole("grid", { name: /Board/ });
  for (const card of ["Ks", "Js", "Th", "7d", "2c"]) {
    await deck.getByRole("gridcell", { name: card }).click();
  }
  await page.locator('input[id^="range-text-Player 0"]').fill("QQ+");
  await page.locator('input[id^="range-text-Player 0"]').press("Enter");
  await page.locator('input[id^="range-text-Player 1"]').fill("JJ+");
  await page.locator('input[id^="range-text-Player 1"]').press("Enter");
  await page.locator("#name").fill("kbd-lib");
  await page.locator("#solver-opts").fill('{"fake_real_keys": true, "fake_iter_seconds": 0.01, "fake_infosets": 40}');
  await page.locator("#iters").fill("40");
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await expect(page.getByTestId("job-status")).toContainText("done", { timeout: 60_000 });
  await expect(page.getByTestId("tree-node")).toBeVisible();

  // Keyboard through the viewer: breadcrumb buttons and child edges are all
  // native <button>s — focus + Enter drives navigation.
  const child = page.getByTestId("children").getByRole("button").first();
  await expect(child).toBeEnabled();
  await child.focus();
  await page.keyboard.press("Enter");
  const crumbs = page.getByRole("navigation", { name: "node path" }).getByRole("button");
  await expect(crumbs.last()).not.toHaveText("root");
  await crumbs.first().focus();
  await page.keyboard.press("Enter");
  await expect(crumbs.last()).toHaveText("root");

  // Spot library by keyboard: save a spot then rename it via Tab/Enter/typing.
  await page.goto("/ui/spots/new");
  const deck2 = page.getByRole("grid", { name: /Board/ });
  for (const card of ["As", "Qs", "9h", "5d", "3c"]) {
    await deck2.getByRole("gridcell", { name: card }).click();
  }
  await page.locator('input[id^="range-text-Player 0"]').fill("TT+");
  await page.locator('input[id^="range-text-Player 0"]').press("Enter");
  await page.locator('input[id^="range-text-Player 1"]').fill("JJ+");
  await page.locator('input[id^="range-text-Player 1"]').press("Enter");
  await page.locator("#name").fill("kbd-saved");
  const save = page.getByRole("button", { name: "Save to library" });
  await save.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/ui\/spots$/);

  const row = page.locator("tr", { hasText: "kbd-saved" }).first();
  const renameBtn = row.getByRole("button", { name: "Rename" });
  await renameBtn.focus();
  await page.keyboard.press("Enter");
  // Once editing, the row's name is an input value, not text — re-scope.
  const editRow = page.locator("tr", { has: page.getByLabel("rename spot") });
  await page.getByLabel("rename spot").focus();
  await page.keyboard.type("kbd-saved-v2");
  const saveBtn = editRow.getByRole("button", { name: "Save", exact: true });
  await saveBtn.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("table")).toContainText("kbd-saved-v2");
});
