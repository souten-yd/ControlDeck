import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 機能と拡張: 「Playwright のブラウザ」の項目と導入ボタン、OS のパッケージ（sudo が要る）はコピーできるコマンドだけ。
 *  API は page.route で模擬する（実際には Playwright のブラウザを取得しない・sudo を実行しない）。
 *  根拠: 2026-11-23 利用者「設定の機能の追加のところに導入ボタンを」「追加で入れないといけないものについては導入ボタンを」。 */
const base = { route_gated: false, requires: "", requires_installed: true, available: true, managed: false, enabled: false,
  requested_enabled: true, version: "", executable: "", preview: false };
const features = [
  { ...base, id: "playwright-browsers", name: "Playwright のブラウザ（画面の試験・画面確認係・録画）", kind: "playwright-browsers", category: "search",
    summary: "画面の試験、Workflow のブラウザ操作、画面確認係の録画で使うブラウザ。版ごとに別の置き場へ入れる。全部で約 1GB",
    installed: false, health: "not-installed", error: "足りない: 画面の試験（E2E）: chromium-1228, webkit-2311",
    components: [{ id: "e2e", label: "画面の試験（E2E）", applicable: true, store: "/data/cache/ms-playwright", present: [], missing: ["chromium-1228", "webkit-2311"] }] },
  { ...base, id: "system-ocr", name: "PDF・画像の文字の読み取り（poppler・tesseract）", kind: "system-packages", category: "search",
    summary: "PDF の本文の取り出しと画像の OCR。sudo が要るので、コマンドをコピーして自分で実行する",
    installed: false, health: "not-installed", error: "", missing_packages: ["tesseract-ocr-jpn"], install_command: "sudo apt install -y tesseract-ocr-jpn" },
];

for (const width of [1280, 320]) {
  test(`機能と拡張: Playwright のブラウザ（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/features$/, (route) => route.fulfill({ json: features }));
    await page.route(/\/api\/v1\/features\/auto-update$/, (route) => route.fulfill({ json: { enabled: false, time: "04:00", last: {}, running: false } }));
    let installRequested = false;
    await page.route(/\/api\/v1\/features\/playwright-browsers\/install-jobs$/, (route) => {
      installRequested = true;
      return route.fulfill({ status: 201, json: { job_id: "job-pw" } });
    });
    await page.route(/\/api\/v1\/jobs\/job-pw$/, (route) => route.fulfill({ json: { id: "job-pw", status: "running", progress: { status: "ブラウザを導入中" } } }));

    await page.goto("/settings/features");
    const card = page.locator("#feature-playwright-browsers");
    await expect(card.getByText("Playwright のブラウザ（画面の試験・画面確認係・録画）")).toBeVisible();
    await expect(card.getByText(/足りない: 画面の試験（E2E）/)).toBeVisible();
    const button = card.getByRole("button", { name: "導入", exact: true });
    await expect(button).toBeEnabled();

    const ocr = page.locator("#feature-system-ocr");
    await expect(ocr.getByText("sudo apt install -y tesseract-ocr-jpn")).toBeVisible();
    await expect(ocr.getByRole("button", { name: "コマンドをコピー" })).toBeVisible();
    await expect(ocr.getByRole("button", { name: "導入", exact: true })).toHaveCount(0);

    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-playwright-browsers-${width}.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);

    await button.click();
    await expect.poll(() => installRequested).toBe(true);
  });
}
