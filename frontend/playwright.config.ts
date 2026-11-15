import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  // 失敗した spec の trace・画面写真は test-results/、一覧は playwright-report/ に残す。
  // ./deck.sh e2e は本番とは別の検証用インスタンスを起こして CONTROL_DECK_E2E_URL を渡す。
  reporter: [["list"], ["html", { open: "never" }]],
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
