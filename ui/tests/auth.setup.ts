import { test as setup, expect } from "@playwright/test";

const KEY = process.env.HOLDEM_UI_TEST_KEY ?? "e2e-ui-key";
const AUTH_FILE = "playwright/.auth/session.json";

// Log in once and reuse the session cookie: POST /v1/auth/login is rate
// limited (10/min), so a login per test would flake under a full run.
setup("authenticate", async ({ page }) => {
  await page.goto("/ui/login");
  await page.getByLabel("API key").fill(KEY);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/ui\/jobs/);
  await page.context().storageState({ path: AUTH_FILE });
});
