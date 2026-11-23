import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュース: 画像の無い記事のアクセントライン・ホームのカード（テロップ・まとめの概要・表示数）・プリセット・取得の失敗の件数を出さないこと。API は page.route で差し替える。
 *  根拠: 2026-11-23 利用者「無意味なアイコンを貼ったデカデカとしたエリア…アクセントラインだけにして、表示を小さくする」「取れなかった総数が1件ありますと表示されてますけれども、ニュースのところで表示しないでほしい」
 *  「ニュースのソースの右側にプリセットボタン」「ホームのニュースに、今日（無ければ昨日）のまとめの概要と、記事を何個か。何個かは設定できるように」。
 *  撮影は test-results/news-noimg-*.png・news-home-digest-{today,yesterday,none}-{390,1280}.png・news-presets-{320,1280}.png。 */

const noState = () => ({ open: false, like: false, dislike: false, hide: false, star: false });
const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
const day = (offset: number) => { const d = new Date(); d.setDate(d.getDate() + offset); return d.toLocaleDateString("en-CA"); };
const art = (c: string) => "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="${c}"/></svg>`);
const item = (id: number, title: string, image: string | null) => ({
  id, kind: "news", source: "テスト", source_key: "rss:t", title, url: `https://example.com/n/${id}`, summary: "抜粋", image_url: image,
  published_at: at(id / 2), topics: [], state: noState(), channel: "c1",
});
const ITEMS = Array.from({ length: 10 }, (_, i) => item(i + 1, `記事 ${i + 1}`, i % 2 === 0 ? art("#38bdf8") : null));
const CHANNELS = [{ id: "c1", name: "テスト", color: "#f5c518", include: ["記事"], exclude: [], kinds: [], lang: "both", limit: 6, search: true }];
const digest = (d: string, body: string) => ({ id: 1, period: "daily", label: "今朝のまとめ", day: d, body, model: "m", created_at: at(1), error: "", fallback: false, item_ids: [1] });
const NEW_BODY = "## 今日の 3 行\n- Qwen の新モデルが公開された。[1]\n- llama.cpp が高速化した。[2]\n- ROCm の更新が出た。[3]\n\n## テスト\n- 詳細。[1]";

async function mock(page: Page, opts: { digestDay?: number | null; homeItems?: number } = {}) {
  const prefs = { display: { design: "broadcast", accent: "", font_size: "m", density: "normal", images: true, columns: "auto", ticker: true, onair: true, numbers: true, reduce_motion: false, home_items: opts.homeItems ?? 5 },
    taste: { strength: 0.5, use_chat: true, half_life_days: 30, mute_terms: [], mute_sources: [], dim_read: true } };
  const saved: unknown[] = [];
  const latest = opts.digestDay == null ? null : digest(day(opts.digestDay), NEW_BODY);
  await page.route(/\/api\/v1\/news\/items\?.*/, (r) => r.fulfill({ json: { items: ITEMS, next: null } }));
  await page.route(/\/api\/v1\/news\/prefs$/, (r) => { if (r.request().method() === "PUT") { Object.assign(prefs, r.request().postDataJSON()); saved.push(r.request().postDataJSON()); } return r.fulfill({ json: prefs }); });
  await page.route(/\/api\/v1\/news\/channels$/, (r) => r.fulfill({ json: { channels: CHANNELS, archive_keep: 50000, interval_minutes: 30, kinds: {}, reddit_key: false, limits: { channels: 12, terms: 20, palette: ["#f5c518"] } } }));
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily: latest, weekly: null }, digests: latest ? [latest] : [] } }));
  await page.route(/\/api\/v1\/news\/settings$/, (r) => r.fulfill({ json: { settings: { feed_refresh_minutes: 0, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: false, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" }, llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto" }, rag: { enabled: false, fulltext: false, on_prune: "keep" } }, weekdays: [], interval_minutes: 30 } }));
  await page.route(/\/api\/v1\/news\/sources$/, (r) => r.fulfill({ json: {
    config: { enabled: { release: true, news: true, model: true, forum: true, reddit: true, event: true }, github: [], codeberg: [], rss: [], hackernews: true, huggingface: true, discourse: [], reddit: [] },
    kinds: { news: "ニュース" }, reddit_key: false, limit: 200,
    sources: [{ source_key: "rss:bad", label: "Phoronix", kind: "news", ok: false, skipped: "", error: "HTTP 500", count: 0, at: at(0) }], last: { finished: at(0.2), new: 3 }, running: false } }));
  await page.route(/\/api\/v1\/news\/presets$/, (r) => r.fulfill({ json: { verified: "2026-11-23",
    groups: [{ id: "local-ai", label: "ローカル AI", hint: "", count: 2 }, { id: "game", label: "テレビゲーム", hint: "", count: 1 }],
    items: [
      { id: "github:ollama/ollama", group: "local-ai", kind: "github", name: "ollama/ollama", value: "ollama/ollama", lang: "en", note: "Release", key: false, verified: "2026-11-23" },
      { id: "reddit:LocalLLaMA", group: "local-ai", kind: "reddit", name: "r/LocalLLaMA", value: "LocalLLaMA", lang: "en", note: "API キーが要る", key: true, verified: "2026-11-23" },
      { id: "rss:https://www.4gamer.net/rss/index.xml", group: "game", kind: "rss", name: "4Gamer.net", value: "https://www.4gamer.net/rss/index.xml", lang: "ja", note: "", key: false, verified: "2026-11-23" }] } }));
  return { saved };
}

test("ニュース: 画像の無い記事は大きな箱でなく細いアクセントラインで、取得の失敗の件数は出ない", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await mock(page);
    await establishSession(page, context);
    await page.goto("/news");
    await expect(page.getByText(/取れなかったソース/)).toHaveCount(0);
    const lines = page.locator("[data-noimg='true']");
    await expect(lines.first()).toBeVisible();
    const box = await lines.first().boundingBox();
    expect(box!.height).toBeLessThanOrEqual(8);             // 箱ではなく線
    await expect(page.locator(".nw-fallback")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/news-noimg-${width}.png`, fullPage: true });
    await page.unroute(/.*/);
  }
});

for (const [name, offset] of [["today", 0], ["yesterday", -1], ["none", null]] as const) {
  test(`ニュース: ホームのカード（まとめ: ${name}）はテロップ・まとめの概要・記事 5 件`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await mock(page, { digestDay: offset });
      await establishSession(page, context);
      await page.goto("/");
      const card = page.getByRole("region", { name: "ニュース" });
      await expect(card.getByTestId("home-telop")).toBeVisible();
      const digestBox = card.getByTestId("home-digest");
      if (offset === null) {
        await expect(digestBox).toHaveCount(0);
        await expect(card.getByTestId("home-telop-text")).toContainText("記事 1");        // まとめが無ければ新しい見出し
      } else {
        await expect(digestBox).toContainText("Qwen の新モデルが公開された。");
        await expect(digestBox).not.toContainText("[1]");
        await expect(digestBox).toContainText(offset === 0 ? "今朝のまとめ" : "昨日のまとめ");
      }
      await expect(card.getByTestId("home-lead")).toHaveCount(1);
      await expect(card.getByTestId("home-rows").locator("li")).toHaveCount(4);          // 全体で 5 件（1 位 + 4 行）
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `test-results/news-home-digest-${name}-${width}.png`, fullPage: true });
      await page.unroute(/.*/);
    }
  });
}

test("ニュース: テロップは押すと止まり、動きを減らす設定では流さない", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mock(page, { digestDay: 0 });
  await establishSession(page, context);
  await page.goto("/");
  const card = page.getByRole("region", { name: "ニュース" });
  const stop = card.getByRole("button", { name: "テロップを止める" });
  await stop.click();
  await expect(card.getByRole("button", { name: "テロップを流す" })).toHaveAttribute("aria-pressed", "true");
  await expect(card).toHaveAttribute("data-paused", "true");
  await card.getByRole("button", { name: "テロップを流す" }).click();
  await expect(card).toHaveAttribute("data-paused", "false");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const anim = await card.locator(".nw-ticker-track").evaluate((el) => getComputedStyle(el).animationName);
  expect(anim).toBe("none");
});

test("ニュース: 設定でホームに出す記事の数を変えると表示が変わる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  const { saved } = await mock(page, { digestDay: 0, homeItems: 3 });
  await establishSession(page, context);
  await page.goto("/");
  const card = page.getByRole("region", { name: "ニュース" });
  await expect(card.getByTestId("home-rows").locator("li")).toHaveCount(2);              // 3 件 = 1 位 + 2 行
  await page.goto("/settings/news");
  const input = page.getByLabel("ホームに出す記事の数");
  await input.fill("7");
  await expect.poll(() => saved.length).toBeGreaterThan(0);
  expect((saved[saved.length - 1] as { display: { home_items: number } }).display.home_items).toBe(7);
});

test("ニュース: 設定 → ソースのプリセットで分野ごとに足し外しできる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await mock(page);
    await page.route(/\/api\/v1\/news\/sources$/, async (r) => {
      if (r.request().method() === "PUT") return r.fulfill({ json: { config: r.request().postDataJSON() } });
      return r.fallback();
    });
    await establishSession(page, context);
    await page.goto("/settings/news");
    await page.getByTestId("news-presets-button").click();
    const panel = page.getByTestId("news-presets");
    await expect(panel.getByText("ollama/ollama")).toBeVisible();
    await panel.getByRole("checkbox", { name: /ollama\/ollama/ }).check();
    await expect(panel.getByRole("checkbox", { name: /ollama\/ollama/ })).toBeChecked();
    await panel.getByRole("tab", { name: /テレビゲーム/ }).click();
    await expect(panel.getByText("4Gamer.net")).toBeVisible();
    await page.getByTestId("news-source-status").locator("summary").click();
    await expect(page.getByTestId("news-source-status")).toContainText("Phoronix: HTTP 500");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/news-presets-${width}.png`, fullPage: true });
    await page.unroute(/.*/);
  }
});
