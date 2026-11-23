import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 設定 → ニュースの「下ごしらえの使うモデル」（既定は CPU の Gemma 4 E2B）と、機能と拡張の「ニュースの下ごしらえ（CPU の LLM）」。API は page.route で模擬する（モデルは取得しない）。
 *  根拠: 2026-11-24 利用者「基本的に CPU で動かした Gemma の小を使う。生成クオリティの問題なかったということなので、それを既定に設定する」。
 *  撮影は test-results/settings-news-cpu-llm-{320,1280}.png・settings-news-cpu-llm-feature-{320,1280}.png。 */
const cpu = { id: "cpu:gemma-4-e2b", label: "CPU（Gemma 4 E2B、GPU を使わない・約 4GB の RAM・1 件約 7 秒）", installed: false, runtime_ready: true, megabytes: 2841, ram_gb: 4, seconds_per_item: 7 };
const settings = (cpuLlm: object) => ({
  settings: {
    feed_refresh_minutes: 15, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" },
    llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto", digest_focus: "", prep_enabled: true, prep_model: "cpu:gemma-4-e2b", prep_max_per_run: 100 }, rag: { enabled: true, fulltext: false, on_prune: "keep" },
    imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 },
  },
  weekdays: ["月", "火", "水", "木", "金", "土", "日"], interval_minutes: 30, prep_calls_today: 12, imagegen_available: false, imagegen_tiers: [], cpu_llm: cpuLlm,
});
const base = { route_gated: false, requires: "llama-cpp", requires_installed: true, available: true, managed: false, enabled: false, requested_enabled: true,
  version: "", executable: "", preview: false, error: "" };
const feature = { ...base, id: "news-cpu-llm", name: "ニュースの下ごしらえ（CPU の LLM）", kind: "cpu-llm", category: "tools", summary: "ニュース記事の日本語の概要・画像の描写・話題の語を、CPU と RAM だけで作る小さな LLM…",
  installed: false, health: "not-installed" };

for (const width of [1280, 320]) {
  test(`ニュースの下ごしらえ: 既定は CPU の Gemma（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    let body = settings(cpu);
    await page.route(/\/api\/v1\/news\/settings$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: body }) : route.continue());
    await page.goto("/settings/news");
    const select = page.getByLabel("下ごしらえのモデル");
    await expect(select).toHaveValue("cpu:gemma-4-e2b");
    await expect(select.locator("option:checked")).toContainText("CPU（Gemma 4 E2B、GPU を使わない・約 4GB の RAM・1 件約 7 秒）");
    await expect(page.getByTestId("prep-cpu-missing")).toBeVisible();                      // 未導入 → 案内
    await expect(page.getByTestId("prep-calls")).toContainText("今日の下ごしらえの呼び出し 12 回");
    await expect(page.getByTestId("prep-calls")).not.toContainText("OpenRouter");          // CPU のときは回数の上限の注意は要らない
    await page.getByTestId("prep-cpu-missing").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-news-cpu-llm-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    // 導入済み → 案内が消える。ほかのモデルも選べて、選ぶと OpenRouter の注意が出る
    body = settings({ ...cpu, installed: true });
    await page.reload();
    await expect(page.getByTestId("prep-cpu-missing")).toHaveCount(0);
    await expect(page.getByTestId("prep-cpu-note")).toContainText("GPU は使いません");
    await page.getByLabel("下ごしらえのモデル").selectOption("auto");
    await expect(page.getByTestId("prep-calls")).toContainText("OpenRouter");
  });

  test(`機能と拡張: ニュースの下ごしらえ（CPU の LLM）の導入（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/features$/, (route) => route.fulfill({ json: [feature] }));
    await page.route(/\/api\/v1\/features\/auto-update$/, (route) => route.fulfill({ json: { enabled: false, time: "04:00", last: {}, running: false } }));
    let requested = "";
    await page.route(/\/api\/v1\/features\/news-cpu-llm\/install-jobs$/, (route) => {
      requested = route.request().url();
      return route.fulfill({ status: 201, json: { job_id: "job-cpu" } });
    });
    await page.route(/\/api\/v1\/jobs\/job-cpu$/, (route) => route.fulfill({ json: { id: "job-cpu", status: "running", progress: { status: "モデルを取得中" } } }));
    await page.goto("/settings/features");
    const card = page.locator("#feature-news-cpu-llm");
    await expect(card).toContainText("ニュースの下ごしらえ（CPU の LLM）");
    await card.getByRole("button", { name: "導入" }).click();
    await expect.poll(() => requested).toContain("/features/news-cpu-llm/install-jobs");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-news-cpu-llm-feature-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}
