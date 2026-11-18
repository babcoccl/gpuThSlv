// Real end-to-end UI run on the outpost: login, build a river spot, solve once
// per GPU, check the strategy viewer and the spot library. Every step asserts;
// any failure exits non-zero (npm run e2e:real).
// Screenshots land in scripts/phase10/logs.
// Usage: npm run e2e:real   (reads HOLDEM_API_KEY from ~/.config/holdem-solver/holdem.env)
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.HOLDEM_UI_BASE ?? "http://127.0.0.1:8000";
const OUT = path.resolve(process.env.HOLDEM_UI_LOG_DIR ?? "../scripts/phase10/logs");
const envText = fs.readFileSync(
  path.join(process.env.HOME, ".config/holdem-solver/holdem.env"), "utf8",
);
const KEY = envText.match(/^HOLDEM_API_KEY=(.+)$/m)?.[1]?.trim();
if (!KEY) throw new Error("HOLDEM_API_KEY not found in holdem.env");

fs.mkdirSync(OUT, { recursive: true });

function assert(cond, msg) {
  if (!cond) throw new Error(`assert failed: ${msg}`);
}
async function step(name, fn) {
  process.stdout.write(`step ${name}... `);
  await fn();
  console.log("ok");
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const shot = async (name) => {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  console.log(`  saved ${name}.png`);
};

async function fillSpot(board, name) {
  const deck = page.getByRole("grid", { name: /Board/ });
  for (const card of board) {
    await deck.getByRole("gridcell", { name: card }).click();
    assert(await deck.getByRole("gridcell", { name: card }).getAttribute("aria-selected") === "true",
      `card ${card} not selected`);
  }
  await page.locator('input[id^="range-text-Player 0"]').fill("QQ+, AKs, AKo");
  await page.locator('input[id^="range-text-Player 0"]').press("Enter");
  await page.locator('input[id^="range-text-Player 1"]').fill("JJ+, AQs+, AQo+");
  await page.locator('input[id^="range-text-Player 1"]').press("Enter");
  await page.locator("#name").fill(name);
  await page.locator("#iters").fill("120");
  await page.locator("#pe").fill("10");
}

try {
  await step("login", async () => {
    await page.goto(`${BASE}/ui/login`);
    await shot("01-login");
    await page.getByLabel("API key").fill(KEY);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/ui\/jobs/, { timeout: 15_000 });
  });

  await step("build spot + estimate", async () => {
    await page.goto(`${BASE}/ui/spots/new`);
    await fillSpot(["Ks", "Js", "Th", "7d", "2c"], "phase10-e2e-river");
    await page.getByRole("button", { name: "Estimate" }).click();
    await page.getByTestId("estimate").waitFor({ timeout: 15_000 });
    await shot("02-spot-setup-estimate");
  });

  const jobIds = [];
  await step("solve on both GPUs", async () => {
    for (let i = 0; i < 2; i++) {
      await page.getByRole("button", { name: "Solve", exact: true }).click();
      await page.waitForURL(/\/ui\/jobs\//, { timeout: 15_000 });
      jobIds.push(page.url().split("/").pop());
      if (i === 0) {
        await page.goto(`${BASE}/ui/spots/new`);
        await fillSpot(["As", "Qs", "9h", "5d", "3c"], "phase10-e2e-river-gpu1");
      }
    }
    assert(jobIds.length === 2, "expected two job ids");
  });

  await step("jobs list mid-run", async () => {
    await page.waitForTimeout(4000);
    await page.goto(`${BASE}/ui/jobs`);
    await shot("03-jobs-midrun");
  });

  await step("both jobs done", async () => {
    for (const id of jobIds) {
      await page.goto(`${BASE}/ui/jobs/${id}`);
      await page.getByTestId("job-status").filter({ hasText: "done" }).waitFor({ timeout: 300_000 });
    }
    await page.goto(`${BASE}/ui/jobs`);
    await shot("04-jobs-done");
    const table = await page.getByRole("table").innerText();
    assert(table.includes("done"), "jobs table has no done row");
  });

  await step("strategy viewer root + child", async () => {
    await page.goto(`${BASE}/ui/jobs/${jobIds[0]}`);
    await page.getByTestId("tree-node").waitFor({ timeout: 30_000 });
    await shot("05-strategy-root");
    const child = page.getByTestId("children").getByRole("button").first();
    assert(await child.isEnabled(), "first child edge is disabled");
    await child.click();
    await page.getByTestId("tree-node").waitFor();
    const crumbs = await page.getByRole("navigation", { name: "node path" }).innerText();
    assert(crumbs.includes("root"), "breadcrumb lost root after navigation");
    await shot("06-strategy-child");
  });

  await step("save to spot library", async () => {
    await page.goto(`${BASE}/ui/spots/new`);
    await fillSpot(["As", "Ks", "Qh", "Jd", "Tc"], "phase10-e2e-library");
    await page.locator("#tags").fill("e2e");
    await page.getByRole("button", { name: "Save to library" }).click();
    await page.waitForURL(/\/ui\/spots$/, { timeout: 15_000 });
    const table = await page.getByRole("table").innerText();
    assert(table.includes("phase10-e2e-library"), "saved spot missing from library table");
    await shot("07-spot-library");
  });

  console.log("e2e:real PASS");
} finally {
  await browser.close();
}
