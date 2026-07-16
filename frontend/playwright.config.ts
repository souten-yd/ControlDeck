import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: process.env.CONTROL_DECK_E2E_URL ?? "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    browserName: "chromium",
    viewport: { width: 320, height: 700 },
    hasTouch: true,
    isMobile: true,
  },
});
