import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 設定 → ニュースの「モデルの精度」（小・中・大）と、機能と拡張の段ごとの導入ボタン。API は page.route で模擬する（モデルは取得しない）。
 *  根拠: 2026-11-23 利用者「もうちょい精度の高いモデル使えないか？…このモデルを残しておいて、小中大みたいな感じで、選べるようにしてほしい、設定で」。 */
const tiers = [
  { id: "small", label: "小", installed: true, megabytes: 1700, time_seconds: 1, ram_gb: 3, max_per_run: 30, note: "SDXS-512 DreamShaper。速いが人物や構図は崩れやすい" },
  { id: "medium", label: "中", installed: false, megabytes: 1000, time_seconds: 2, ram_gb: 5, max_per_run: 30, note: "LCM-Dreamshaper v7（int8・4 ステップ）。小より物の形と質感が自然" },
  { id: "large", label: "大", installed: false, megabytes: 4200, time_seconds: 4, ram_gb: 9, max_per_run: 20, note: "SDXL-Lightning（int8・2 ステップ・1024px）。いちばん精細" },
];
const settings = {
  settings: {
    feed_refresh_minutes: 15, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" },
    llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto" }, rag: { enabled: true, fulltext: false, on_prune: "keep" },
    imagegen: { enabled: true, tier: "medium", threads: 8, max_per_run: 30 },
  },
  weekdays: ["月", "火", "水", "木", "金", "土", "日"], interval_minutes: 60, imagegen_available: false, imagegen_tiers: tiers,
};
const base = { route_gated: false, requires: "", requires_installed: true, available: true, managed: true, enabled: true, requested_enabled: true,
  version: "2026.4.1", executable: "/x/python", preview: false, error: "" };
const feature = { ...base, id: "news-imagegen", name: "ニュースの画像生成（CPU）", kind: "python-lib", category: "addon", summary: "画像の無いニュース記事に…",
  installed: true, health: "healthy", model_tiers: tiers };

for (const width of [1280, 320]) {
  test(`ニュースの画像生成: 精度の選択（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/news\/settings$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: settings }) : route.continue());
    await page.goto("/settings/news?tab=image");
    const card = page.getByRole("region", { name: "画像の無い記事のイメージ画像（AI）" });   // Card の id は見出し（h2）に付くので、領域の名前で取る
    await expect(card.getByTestId("imagegen-tier-option-small")).toBeVisible();
    await expect(card.getByTestId("imagegen-tier-option-large")).toContainText("約 4.2GB");
    await expect(card.getByRole("radio", { name: /中/ })).toBeChecked();
    await expect(card.getByTestId("imagegen-missing")).toBeVisible();      // 中は未導入 → 案内
    await card.getByTestId("imagegen-tier-option-small").click();
    await expect(card.getByTestId("imagegen-missing")).toHaveCount(0);
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-news-imagegen-tiers-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });

  test(`ニュースの画像生成: 機能と拡張で段ごとに導入（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/features$/, (route) => route.fulfill({ json: [feature] }));
    await page.route(/\/api\/v1\/features\/auto-update$/, (route) => route.fulfill({ json: { enabled: false, time: "04:00", last: {}, running: false } }));
    let requested = "";
    await page.route(/\/api\/v1\/features\/news-imagegen\/models\/(\w+)\/install-jobs$/, (route) => {
      requested = route.request().url();
      return route.fulfill({ status: 201, json: { job_id: "job-tier" } });
    });
    await page.route(/\/api\/v1\/jobs\/job-tier$/, (route) => route.fulfill({ json: { id: "job-tier", status: "running", progress: { status: "モデルを取得中" } } }));
    await page.goto("/settings/features");
    const card = page.locator("#feature-news-imagegen");
    await card.getByText("詳細と操作").click();
    const list = card.getByTestId("imagegen-tiers");
    await expect(list.getByTestId("imagegen-tier-small")).toContainText("導入済み");
    await expect(list.getByTestId("imagegen-tier-small").getByRole("button")).toHaveCount(0);   // 小は個別には消せない
    await list.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-imagegen-tiers-features-${width}.png` });
    await list.getByRole("button", { name: "大のモデルを導入" }).click();
    await expect.poll(() => requested).toContain("/models/large/install-jobs");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}
