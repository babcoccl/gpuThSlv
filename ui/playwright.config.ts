import { defineConfig } from "@playwright/test";

const port = Number(process.env.HOLDEM_UI_PORT ?? 8317);

export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "main",
      testMatch: /(e2e|a11y)\.spec\.ts/,
      dependencies: ["setup"],
      use: { storageState: "playwright/.auth/session.json" },
    },
  ],
  webServer: {
    command: "bash scripts/serve-fake.sh",
    port,
    timeout: 60_000,
    reuseExistingServer: false,
    cwd: import.meta.dirname,
  },
});
