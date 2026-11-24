import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュースのソース: 「サイトを追加」（RSS の候補・記事の一覧の巡回）と「通信確認」（既存のソース・追加前の自動確認・失敗の表示）。API は page.route で差し替える。
 *  根拠: 2026-11-24 利用者「任意のサイトのクロール機能を追加できるのか、サイト追加後に通信確認みたいなことがあるといい」。
 *  撮影は test-results/news-site-check-{320,1280}.png・news-site-fail-{320,1280}.png。 */

const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
const OK = { ok: true, count: 25, titles: ["一つ目の記事", "二つ目の記事", "三つ目の記事"], ms: 420, status: 200, latest: "2026-11-24", error: null };
const BOT = { ok: false, count: 0, titles: [], ms: 180, status: 403, latest: "", error: { code: "blocked", message: "取れません（ロボット確認）: HTTP 403", status: 403, location: "" } };

async function mock(page: Page) {
  const state = { saved: null as null | { rss: { name: string; url: string }[]; pages: { name: string; url: string }[] }, checks: [] as { kind: string; value: string }[] };
  await page.route(/\/api\/v1\/news\/sources$/, (r) => {
    if (r.request().method() === "PUT") { state.saved = r.request().postDataJSON(); return r.fulfill({ json: { config: state.saved } }); }
    return r.fulfill({ json: {
      config: { enabled: { news: true }, github: [], codeberg: [], rss: [{ name: "既存のフィード", url: "https://old.example/feed.xml" }], pages: [], hackernews: false, huggingface: false, discourse: [], reddit: [] },
      kinds: { news: "ニュース" }, reddit_key: false, limit: 200,
      sources: [{ source_key: "rss:https://old.example/feed.xml", label: "既存のフィード", kind: "news", ok: false, skipped: "", error: "HTTP 403", count: 0, at: at(0), ok_at: at(5), last_error: "HTTP 403" }],
      last: { finished: at(0.2), new: 0 }, running: false } });
  });
  await page.route(/\/api\/v1\/news\/presets$/, (r) => r.fulfill({ json: { verified: "2026-11-24", groups: [], items: [] } }));
  await page.route(/\/api\/v1\/news\/sources\/check$/, (r) => {
    const body = r.request().postDataJSON();
    state.checks.push(body);
    return r.fulfill({ json: String(body.value).includes("bot.example") ? BOT : OK });
  });
  await page.route(/\/api\/v1\/news\/sources\/discover$/, (r) => {
    const url = r.request().postDataJSON().url as string;
    if (url.includes("bot.example")) return r.fulfill({ json: { url, title: "", mode: "none", feeds: [], page: null, error: { code: "blocked", message: "取れません（ロボット確認）: HTTP 403" } } });
    return r.fulfill({ json: { url, title: "テストのサイト", mode: "feeds", page: { count: 12, titles: ["一覧の記事 A", "一覧の記事 B", "一覧の記事 C"], robots: true },
      feeds: [{ url: "https://site.example/feed.xml", count: 20, latest: "2026-11-24", titles: ["RSS の記事 1", "RSS の記事 2", "RSS の記事 3"] },
        { url: "https://site.example/atom.xml", count: 10, latest: "2026-11-23", titles: ["Atom の記事 1"] }], error: null } });
  });
  return state;
}

test("ニュース: サイトを追加 → 候補の RSS を選んで保存・既存のソースの通信確認・最後に取れた時刻", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const state = await mock(page);
    if (width === 1280) await establishSession(page, context);
    await page.goto("/settings/news");
    // 既存のソースの通信確認
    await page.getByTestId("news-check-rss").first().click();
    await expect(page.getByTestId("news-check-result")).toContainText("取れました: 25 件");
    await expect(page.getByTestId("news-check-result")).toContainText("一つ目の記事");
    expect(state.checks[0]).toMatchObject({ kind: "rss", value: "https://old.example/feed.xml" });
    // 状態の一覧に最後に取れた時刻と失敗の理由
    await page.getByTestId("news-source-status").locator("summary").click();
    await expect(page.getByTestId("news-source-status")).toContainText("最後に取れた");
    // サイトを追加 → 候補
    await page.getByTestId("news-site-url").fill("https://site.example/");
    await page.getByTestId("news-site-discover").click();
    const result = page.getByTestId("news-site-result");
    await expect(result).toContainText("RSS の候補が見つかりました");
    await expect(page.getByTestId("news-site-candidate")).toHaveCount(2);
    await expect(page.getByTestId("news-site-page")).toContainText("12 件");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/news-site-check-${width}.png`, fullPage: true });
    await page.getByTestId("news-site-candidate").first().getByRole("button", { name: "この RSS を追加" }).click();
    await expect.poll(() => state.saved?.rss.map((f) => f.url)).toContain("https://site.example/feed.xml");
    expect(state.saved?.rss.find((f) => f.url.includes("site.example"))?.name).toBe("テストのサイト");
    await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});

test("ニュース: 記事の一覧の巡回で追加・取れないフィードは保存の前に警告（それでも保存はできる）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const state = await mock(page);
    if (width === 1280) await establishSession(page, context);
    await page.goto("/settings/news");
    await page.getByTestId("news-site-url").fill("https://site.example/news/");
    await page.getByTestId("news-site-discover").click();
    await page.getByTestId("news-site-page").getByRole("button", { name: "巡回で追加" }).click();
    await expect.poll(() => state.saved?.pages.map((p) => p.url)).toEqual(["https://site.example/news/"]);
    // 取れないサイトの発見は警告
    await page.getByTestId("news-site-url").fill("https://bot.example/");
    await page.getByTestId("news-site-discover").click();
    await expect(page.getByTestId("news-site-result")).toContainText("ロボット確認");
    // フィードを直接追加 → 自動の通信確認で失敗 → 警告 → それでも保存
    await page.getByLabel("フィードの URL").fill("https://bot.example/feed.xml");
    await page.getByRole("button", { name: "フィードを追加" }).click();
    await expect(page.getByTestId("news-precheck-result")).toContainText("取れません（ロボット確認）");
    expect(state.saved?.rss.some((f) => f.url.includes("bot.example"))).toBeFalsy();     // まだ保存していない
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/news-site-fail-${width}.png`, fullPage: true });
    await page.getByTestId("news-feed-force").click();
    await expect.poll(() => state.saved?.rss.some((f) => f.url.includes("bot.example"))).toBe(true);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});
