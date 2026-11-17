// Real end-to-end UI run on the outpost: login, build a small river spot,
// solve once on each GPU, screenshot the jobs list and strategy viewer.
// Screenshots land in scripts/phase10/logs.
// Usage: node ui_e2e.mjs   (reads HOLDEM_API_KEY from ~/.config/holdem-solver/holdem.env)
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.HOLDEM_UI_BASE ?? "http://127.0.0.1:8000";
const OUT = path.resolve(process.env.HOLDEM_UI_LOG_DIR ?? "scripts/phase10/logs");
const envText = fs.readFileSync(
  path.join(process.env.HOME, ".config/holdem-solver/holdem.env"), "utf8",
);
const KEY = envText.match(/^HOLDEM_API_KEY=(.+)$/m)?.[1]?.trim();
if (!KEY) throw new Error("HOLDEM_API_KEY not found in holdem.env");

fs.mkdirSync(OUT, { recursive: true });
const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  console.log(`saved ${name}.png`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// 1. Login
await page.goto(`${BASE}/ui/login`);
await shot(page, "01-login");
await page.getByLabel("API key").fill(KEY);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL(/\/ui\/jobs/);
console.log("logged in");

// 2. Build a small river spot
await page.goto(`${BASE}/ui/spots/new`);
const deck = page.getByRole("grid", { name: /Board/ });
for (const card of ["Ks", "Js", "Th", "7d", "2c"]) {
  await deck.getByRole("gridcell", { name: card }).click();
}
await page.locator('input[id^="range-text-Player 0"]').fill("QQ+, AKs, AKo");
await page.locator('input[id^="range-text-Player 0"]').press("Enter");
await page.locator('input[id^="range-text-Player 1"]').fill("JJ+, AQs+, AQo+");
await page.locator('input[id^="range-text-Player 1"]').press("Enter");
await page.locator("#name").fill("phase10-e2e-river");
await page.locator("#iters").fill("120");
await page.locator("#pe").fill("10");
await page.getByRole("button", { name: "Estimate" }).click();
await page.getByTestId("estimate").waitFor();
await shot(page, "02-spot-setup-estimate");

// 3. Solve twice — scheduler places one job per GPU
const jobIds = [];
for (let i = 0; i < 2; i++) {
  await page.getByRole("button", { name: "Solve", exact: true }).click();
  await page.waitForURL(/\/ui\/jobs\//);
  jobIds.push(page.url().split("/").pop());
  if (i === 0) await page.goto(`${BASE}/ui/spots/new`), await refill(page);
}
async function refill(p) {
  const d = p.getByRole("grid", { name: /Board/ });
  for (const card of ["As", "Qs", "9h", "5d", "3c"]) {
    await d.getByRole("gridcell", { name: card }).click();
  }
  await p.locator('input[id^="range-text-Player 0"]').fill("QQ+, AKs, AKo");
  await p.locator('input[id^="range-text-Player 0"]').press("Enter");
  await p.locator('input[id^="range-text-Player 1"]').fill("JJ+, AQs+, AQo+");
  await p.locator('input[id^="range-text-Player 1"]').press("Enter");
  await p.locator("#name").fill("phase10-e2e-river-gpu1");
  await p.locator("#iters").fill("120");
  await p.locator("#pe").fill("10");
}
console.log("jobs:", jobIds);

// 4. Wait for both done, screenshot jobs list mid-run and at completion
await page.waitForTimeout(4000);
await page.goto(`${BASE}/ui/jobs`);
await shot(page, "03-jobs-midrun");
for (const id of jobIds) {
  await page.goto(`${BASE}/ui/jobs/${id}`);
  await page.getByTestId("job-status").filter({ hasText: "done" }).waitFor({ timeout: 300_000 });
  console.log(`${id} done`);
}
await page.goto(`${BASE}/ui/jobs`);
await shot(page, "04-jobs-done");

// 5. Strategy viewer: root, then a child node
await page.goto(`${BASE}/ui/jobs/${jobIds[0]}`);
await page.getByTestId("tree-node").waitFor();
await shot(page, "05-strategy-root");
const child = page.getByTestId("children").getByRole("button").first();
await child.click();
await page.getByTestId("tree-node").waitFor();
await shot(page, "06-strategy-child");

// 6. Save the spot to the library
await page.goto(`${BASE}/ui/spots/new`);
const d2 = page.getByRole("grid", { name: /Board/ });
for (const card of ["As", "Ks", "Qh", "Jd", "Tc"]) {
  await d2.getByRole("gridcell", { name: card }).click();
}
await page.locator('input[id^="range-text-Player 0"]').fill("TT+");
await page.locator('input[id^="range-text-Player 0"]').press("Enter");
await page.locator('input[id^="range-text-Player 1"]').fill("JJ+");
await page.locator('input[id^="range-text-Player 1"]').press("Enter");
await page.locator("#name").fill("phase10-e2e-library");
await page.locator("#tags").fill("e2e");
await page.getByRole("button", { name: "Save to library" }).click();
await page.waitForURL(/\/ui\/spots$/);
await shot(page, "07-spot-library");

await browser.close();
console.log("e2e complete");
