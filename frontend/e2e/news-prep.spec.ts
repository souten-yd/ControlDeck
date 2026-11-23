import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュース: 設定に「今日の下ごしらえの呼び出し n 回」と上限が出ること、ON AIR がチャンネルに合う記事であること。API は page.route で模擬する。
 *  根拠: 2026-11-24 利用者「ある程度のまとまりを送って構造的に個別に返してもらう」（無料のモデルの 1 日の上限 50 回に気づけるように）、
 *  本番で ON AIR とホームの 1 位が Bored.com（HN のトップ）でチャンネルに合わなかった。
 *  撮影は test-results/news-prep-settings-{320,1280}.png・news-prep-onair-{320,1280}.png。 */

const noState = () => ({ open: false, like: false, dislike: false, hide: false, star: false });
const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
const item = (id: number, title: string, source_key = "rss:t") => ({
  id, kind: "news", source: "テスト", source_key, title, url: `https://example.com/n/${id}`, summary: "概要", image_url: null,
  published_at: at(id), topics: [], state: noState(), channel: "c1",
});
const MATCH = item(1, "Qwen の新モデルが llama.cpp に対応");
const OTHERS = [item(2, "ROCm 7 の更新"), item(3, "AMD の新しい GPU")];
const CHANNELS = [{ id: "c1", name: "ローカル LLM", color: "#f5c518", number: 1 }];
const settings = {
  settings: {
    feed_refresh_minutes: 15, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" },
    llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto", digest_focus: "", prep_enabled: true, prep_model: "auto", prep_max_per_run: 100 }, rag: { enabled: true, fulltext: false, on_prune: "keep" },
    imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 },
  },
  weekdays: ["月", "火", "水", "木", "金", "土", "日"], interval_minutes: 30, prep_calls_today: 37, imagegen_available: false, imagegen_tiers: [],
};

async function mockHome(page: Page) {
  await page.route(/\/api\/v1\/news\/home$/, (r) => r.fulfill({ json: {
    channels: CHANNELS, onair: MATCH, ticker: [MATCH, ...OTHERS].map((i) => ({ id: i.id, title: i.title, source: i.source, url: i.url, published_at: i.published_at })),
    now: OTHERS.slice(0, 1), today: OTHERS.slice(1), week: [],
    sections: [{ id: "c1", name: "ローカル LLM", color: "#f5c518", number: 1, total: 3, week: 3, items: [MATCH, ...OTHERS] }],
    interest: { topics: [], sources: [], avoided_sources: [], muted: [], chat_terms: 0 }, trends: { days: [], topics: [], sources: [] },
    numbers: { today: 3, read_today: 0, read_30d: 0, top_channel: null, top_topic: null, top_topic_count: 0 }, generated_at: new Date().toISOString() } }));
}

for (const width of [1280, 320]) {
  test(`ニュース設定: 今日の下ごしらえの呼び出し回数と上限（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/news\/settings$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: settings }) : route.continue());
    await page.goto("/settings/news");
    await expect(page.getByTestId("prep-calls")).toContainText("今日の下ごしらえの呼び出し 37 回");
    await expect(page.getByTestId("prep-max")).toHaveValue("100");
    await page.getByTestId("prep-calls").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/news-prep-settings-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });

  test(`ニュース: ON AIR はチャンネルに合う記事（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await mockHome(page);
    await page.goto("/news");
    await expect(page.getByRole("region", { name: "ON AIR" }).getByTestId("onair-title")).toContainText("Qwen の新モデル");
    await page.screenshot({ path: `test-results/news-prep-onair-${width}.png` });
  });
}
