import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュース: 画像の無い記事のアクセントライン・ホームのカード（テロップ・まとめの概要・表示数）・取得の失敗の件数を出さないこと。API は page.route で差し替える。
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
  const numbered = CHANNELS.map((c, i) => ({ id: c.id, name: c.name, color: c.color, number: i + 1 }));
  await page.route(/\/api\/v1\/news\/(archive|trends|profile)/, (r) => r.fulfill({ json: { days: [], total: 0, starred: 0, topics: [], sources: [], avoided_sources: [], muted: [], chat_terms: 0 } }));
  await page.route(/\/api\/v1\/news\/home$/, (r) => r.fulfill({ json: {
    channels: numbered, onair: ITEMS[1], ticker: ITEMS.slice(0, 5).map((i) => ({ id: i.id, title: i.title, source: i.source, url: i.url, published_at: i.published_at })),
    now: ITEMS.slice(2, 4), today: ITEMS.slice(4, 6), week: ITEMS.slice(6, 8),
    sections: [{ id: "c1", name: "テスト", color: "#f5c518", number: 1, total: 10, week: 10, items: ITEMS.slice(0, 6).map((i) => ({ ...i, channel: "c1" })) }],
    interest: { topics: [], sources: [], avoided_sources: [], muted: [], chat_terms: 0 }, trends: { days: [], topics: [], sources: [] },
    numbers: { today: 10, read_today: 0, read_30d: 0, top_channel: null, top_topic: null, top_topic_count: 0 }, generated_at: new Date().toISOString() } }));
  await page.route(/\/api\/v1\/news\/items\?.*/, (r) => r.fulfill({ json: { items: ITEMS, next: null } }));
  await page.route(/\/api\/v1\/news\/prefs$/, (r) => { if (r.request().method() === "PUT") { Object.assign(prefs, r.request().postDataJSON()); saved.push(r.request().postDataJSON()); } return r.fulfill({ json: prefs }); });
  await page.route(/\/api\/v1\/news\/channels$/, (r) => r.fulfill({ json: { channels: CHANNELS, archive_keep: 50000, interval_minutes: 30, kinds: {}, reddit_key: false, limits: { channels: 12, terms: 20, palette: ["#f5c518"] } } }));
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily: latest, weekly: null }, digests: latest ? [latest] : [] } }));
  await page.route(/\/api\/v1\/news\/settings$/, (r) => r.fulfill({ json: { settings: { feed_refresh_minutes: 0, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: false, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" }, llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto" }, rag: { enabled: false, fulltext: false, on_prune: "keep" }, imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 } }, weekdays: [], interval_minutes: 30 } }));
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
  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await mock(page);
    if (width === 1280) await establishSession(page, context);
    await page.goto("/news");
    await expect(page.getByText(/取れなかったソース/)).toHaveCount(0);
    const lines = page.locator("[data-noimg='true']");
    await expect(lines.first()).toBeVisible();
    const box = await lines.first().boundingBox();
    expect(box!.height).toBeLessThanOrEqual(8);             // 箱ではなく線
    await expect(page.locator(".nw-fallback")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/news-noimg-${width}.png`, fullPage: true });
    await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});

for (const [name, offset] of [["today", 0], ["yesterday", -1], ["none", null]] as const) {
  test(`ニュース: ホームのカード（まとめ: ${name}）はテロップ・まとめの概要・記事 5 件`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await mock(page, { digestDay: offset });
      if (width === 1280) await establishSession(page, context);
      await page.goto("/");
      const card = page.getByRole("region", { name: "ニュース" });
      await expect(card.getByTestId("home-telop")).toBeVisible();
      const digestBox = card.getByTestId("home-digest");
      if (offset === null) {
        await expect(digestBox).toHaveCount(0);
        await expect(card.getByTestId("home-telop")).toContainText("記事 1");        // まとめが無ければ新しい見出し
      } else {
        await expect(digestBox).toContainText("Qwen の新モデルが公開された。");
        await expect(digestBox).not.toContainText("[1]");
        await expect(digestBox).toContainText(offset === 0 ? "今朝のまとめ" : "昨日のまとめ");
      }
      await expect(card.getByTestId("home-lead")).toHaveCount(1);
      await expect(card.getByTestId("home-rows").locator("li")).toHaveCount(4);          // 全体で 5 件（1 位 + 4 行）
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `test-results/news-home-digest-${name}-${width}.png`, fullPage: true });
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });
}

test("ニュース: テロップ（共通の Marquee）は複数の項目をめくり、触れると止まり、動きを減らす設定では省略表示になる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await mock(page, { digestDay: 0 });
  await establishSession(page, context);
  await page.goto("/");
  const card = page.getByRole("region", { name: "ニュース" });
  const marquee = card.getByTestId("home-telop").getByTestId("marquee");
  await expect(marquee).toHaveAttribute("data-count", "3");              // 「今日の 3 行」を 1 項目ずつ
  await expect(marquee.locator("[data-marquee]")).toBeVisible();
  const state = () => marquee.locator("[data-inner]").first().evaluate((el) => el.getAnimations().map((a) => a.playState)[0] ?? "none");
  await marquee.hover();
  await expect.poll(state).toBe("paused");
  await page.mouse.move(0, 0);
  await expect.poll(state).not.toBe("paused");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const slide = card.getByTestId("home-telop").locator("[data-marquee]");
  await expect(slide).toBeVisible();
  expect(await slide.evaluate((el) => getComputedStyle(el).textOverflow)).toBe("ellipsis");
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
