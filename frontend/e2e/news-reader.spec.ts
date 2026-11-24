import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";
import { CHANNELS, ITEMS, art, buildHome, mockNews } from "./support/news-mock";

/** ニュースの「あなたの興味」の × とニュースリーダー。API は page.route でモック（外へ通信しない）。
 *  目的: ①興味の × を押すと消え、読み直しても戻らず、「外した語」から戻せる ②記事を押すと外へ飛ばずリーダーが開き、
 *       要約 → 元の記事のリンク → 本文の順に並び、取り出した本文がすぐ出て、訳の途中（訳 n/m）→ 完了へ順に差し替わる
 *       ③取り出せないときは元の記事へ移り、一覧は直リンク ④release は今まで通り外へ ⑤携帯（320・390）・PC（1280）・ライト・ダークで崩れない。
 *  根拠: 2026-11-24 利用者「あなたの興味…それ × をしても消えない」「記事を押すと元ページへ飛ぶ…本文を取得して、英語なら日本語に翻訳して、リーダーみたいな形で
 *       表示…冒頭にその全文の要約、その下に元リンク先のアドレス、翻訳文をその下から」「LLM で変換が終わったものから順次変えていけば、見れない不具合は発生しない」。 */

const SHOTS = process.env.NEWS_SHOTS_DIR ?? "test-results";
const SRC = ["The new model improves accuracy while using less compute.", "It was trained on a large public corpus.", "Benchmarks show a clear gain on coding tasks."];
const JA = ["新しいモデルは、少ない計算で精度を高める。", "大規模な公開コーパスで学習された。", "ベンチマークではコーディングで明確な向上が見られた。"];

/** 1 回読むごとに進む reader の API（取り出し中 → 訳 0/3 → 1/3 → 3/3）。 */
function readerStates(failing = false) {
  const item = { id: 1, title: ITEMS[0].title, source: ITEMS[0].source, kind: "news", published_at: ITEMS[0].published_at, url: "https://example.com/n/1", image_url: art("#f5c518", "#7c3aed", "Qwen") };
  const base = { item, excerpt: "ローカルで動かせるサイズの量子化版が最初から用意されている。", domain: "example.com", source_url: "https://example.com/n/1", lang: "other", error: "", llm_error: "", model: "cpu:gemma-4-e2b", remaining: 0 };
  const blocks = (n: number) => [
    { t: "h", level: 2, src: "Results", ja: n >= 1 ? "結果" : null, fail: false },
    ...SRC.map((src, i) => ({ t: "p", src, ja: n >= i + 2 ? JA[i] : null, fail: false })),
    { t: "ul", src: "speed\nmemory", ja: n >= 5 ? "速度\nメモリ" : null, fail: false },
    { t: "code", src: "print('hello')" },
  ];
  const total = 5;
  const seq = failing ? [] : [
    { ...base, status: "fetching", working: true, phase: "fetching", blocks: [], summary: "", progress: { done: 0, total: 0 } },
    { ...base, status: "ready", working: true, phase: "summarizing", blocks: blocks(0), summary: "", progress: { done: 0, total },},
    { ...base, status: "ready", working: true, phase: "translating", blocks: blocks(2), summary: "新しいモデルは少ない計算で高い精度を出す。\n公開コーパスで学習され、コーディングで向上した。", progress: { done: 2, total } },
    { ...base, status: "ready", working: false, phase: "", blocks: blocks(5), summary: "新しいモデルは少ない計算で高い精度を出す。\n公開コーパスで学習され、コーディングで向上した。", progress: { done: total, total } },
  ];
  const failed = { ...base, status: "failed", working: false, phase: "", blocks: [], summary: "", progress: { done: 0, total: 0 }, error: "blocked: HTTP 403" };
  return { seq, failed, total };
}

async function setup(page: Page, context: Parameters<typeof establishSession>[1], opts: { failing?: boolean; width?: number; height?: number; login?: boolean } = {}) {
  await page.setViewportSize({ width: opts.width ?? 390, height: opts.height ?? 844 });
  const mocks = await mockNews(page);
  const { seq, failed } = readerStates(opts.failing);
  const state = { calls: 0, muted: [] as string[], retried: 0 };
  await page.route(/\/api\/v1\/news\/items\/1\/reader$/, (route) => {
    const page = opts.failing ? failed : seq[Math.min(state.calls, seq.length - 1)];
    state.calls += 1;
    return route.fulfill({ json: page });
  });
  await page.route(/\/api\/v1\/news\/items\/1\/reader\/retry$/, (route) => { state.retried += 1; return route.fulfill({ json: failed }); });
  // 興味の外した語（本番と同じく、ホームと profile の両方がこの状態から作られる）
  const interest = () => {
    const all = [["Qwen", 4.2], ["GGUF", 3.1], ["R9700", 2.4], ["ROCm", 1.6]] as const;
    return { topics: all.filter(([t]) => !state.muted.includes(t)).map(([topic, weight]) => ({ topic, weight })), sources: [], avoided_sources: [], muted: state.muted, chat_terms: 0 };
  };
  await page.route(/\/api\/v1\/news\/profile\/mute$/, (route) => {
    const body = route.request().postDataJSON() as { topic: string; muted: boolean };
    state.muted = state.muted.filter((t) => t !== body.topic);
    if (body.muted) state.muted.push(body.topic);
    return route.fulfill({ json: interest() });
  });
  await page.route(/\/api\/v1\/news\/profile$/, (route) => route.fulfill({ json: interest() }));
  if (opts.login !== false) await establishSession(page, context);
  return { mocks, state, interest };
}

test("ニュース: 興味の × で語が消え、読み直しても戻らず、「外した語」から戻せる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 390, height: 844 });
  await mockNews(page);
  let muted: string[] = [];
  const interest = () => ({ topics: ["Qwen", "GGUF", "R9700"].filter((t) => !muted.includes(t)).map((topic, i) => ({ topic, weight: 4 - i })), sources: [], avoided_sources: [], muted, chat_terms: 0 });
  // ホームの 1 回の読み込みの interest を、外した語の状態から作る（本番の /news/home と同じ作り）
  await page.route(/\/api\/v1\/news\/home$/, async (route) => {
    return route.fulfill({ json: { ...buildHome(ITEMS, CHANNELS), interest: interest() } });
  });
  await page.route(/\/api\/v1\/news\/profile$/, (route) => route.fulfill({ json: interest() }));
  await page.route(/\/api\/v1\/news\/profile\/mute$/, (route) => {
    const body = route.request().postDataJSON() as { topic: string; muted: boolean };
    muted = muted.filter((t) => t !== body.topic);
    if (body.muted) muted.push(body.topic);
    return route.fulfill({ json: interest() });
  });
  await establishSession(page, context);
  await page.goto("/news");
  const panel = page.getByRole("region", { name: "あなたの興味" });
  await expect(panel.getByRole("button", { name: "「Qwen」を興味から外す" })).toBeVisible();
  await panel.getByRole("button", { name: "「Qwen」を興味から外す" }).click();
  await expect(panel.getByRole("button", { name: "「Qwen」を興味から外す" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "「Qwen」を戻す" })).toBeVisible();
  await page.reload();
  await expect(panel.getByRole("button", { name: "「GGUF」を興味から外す" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "「Qwen」を興味から外す" })).toHaveCount(0);
  await panel.getByRole("button", { name: "「Qwen」を戻す" }).click();
  await expect(panel.getByRole("button", { name: "「Qwen」を興味から外す" })).toBeVisible();
});

test("ニュース: 記事を押すとリーダーが開き、要約 → 元の記事 → 本文の順で、訳が段落ごとに差し替わる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await setup(page, context);
  await page.goto("/news");
  await page.getByTestId("onair-title").getByRole("link").click();
  await expect(page).toHaveURL(/\/news\/read\/1$/);
  await expect(page.getByTestId("reader-title")).toHaveText(ITEMS[0].title);
  // 取り出し中 → 本文（原文）がすぐ出る → 訳が進む → 完了
  await expect(page.getByTestId("reader-summary")).toBeVisible();
  await expect(page.getByTestId("reader-block").first()).toBeVisible();
  await expect(page.getByTestId("reader-progress")).toContainText(/訳 \d\/5/);
  await expect(page.getByTestId("reader-progress")).toContainText("訳 5/5", { timeout: 15_000 });
  await expect(page.getByTestId("reader-summary")).toContainText("新しいモデルは少ない計算で高い精度を出す。");
  await expect(page.getByTestId("reader-body")).toContainText("新しいモデルは、少ない計算で精度を高める。");
  await expect(page.locator("[data-state='pending']")).toHaveCount(0);
  // 並び順: 要約 → 元の記事 → 本文
  const y = async (id: string) => (await page.getByTestId(id).boundingBox())!.y;
  expect(await y("reader-summary")).toBeLessThan(await y("reader-origin"));
  expect(await y("reader-origin")).toBeLessThan(await y("reader-body"));
  const origin = page.getByTestId("reader-open-original");
  await expect(origin).toHaveAttribute("href", "https://example.com/n/1");
  await expect(origin).toHaveAttribute("target", "_blank");
  await expect(page.getByTestId("reader-origin")).toContainText("example.com");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth));
});

test("ニュース: リーダーの途中の状態の撮影（320・390・1280、ライト・ダーク）", async ({ page, context }) => {
  test.setTimeout(120_000); // 6 通り（幅 3 × テーマ 2）を 1 本で撮るので、既定の 30 秒では足りない
  test.skip(!hasSession(), "ログイン情報がありません");
  await establishSession(page, context);
  for (const [width, height] of [[320, 700], [390, 844], [1280, 900]] as const) {
    for (const theme of ["light", "dark"] as const) {
      await setup(page, context, { width, height, login: false });
      await page.addInitScript((t) => localStorage.setItem("cd-theme", t), theme);
      await page.goto("/news/read/1");
      await expect(page.getByTestId("reader-progress")).toContainText("訳 2/5", { timeout: 15_000 });
      await page.screenshot({ path: `${SHOTS}/reader-mid-${width}-${theme}.png`, fullPage: true });
      await expect(page.getByTestId("reader-progress")).toContainText("訳 5/5", { timeout: 15_000 });
      await page.screenshot({ path: `${SHOTS}/reader-done-${width}-${theme}.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  }
});

test("ニュース: 本文を取り出せないときは失敗の画面を挟まず元の記事へ移り、一度失敗した記事は一覧から直リンク。release も外へ", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  const { state } = await setup(page, context, { failing: true, width: 320, height: 700 });
  // 元の記事のページ（外へは通信しない）
  await page.route("https://example.com/**", (route) => route.fulfill({ contentType: "text/html", body: "<html><title>original</title><body>original page</body></html>" }));
  await page.goto("/news/read/1");
  await page.waitForURL("https://example.com/n/1");
  await expect(page.locator("body")).toContainText("original page");
  expect(state.retried).toBe(0);
  // 一度失敗した記事（id 3）は、リーダーを通さず元の記事への直リンク（新しいタブ）。成功する記事は今までどおりリーダー
  await page.route(/\/api\/v1\/news\/items\?.*/, (route) => route.fulfill({ json: { items: ITEMS.map((i) => ({ ...i, state: { ...i.state }, reader_failed: i.id === 3 })), next: null } }));
  await page.goto("/news");
  await page.getByRole("tab", { name: "新着" }).click();
  const failedLink = page.locator("[data-news-card]", { hasText: "AMD Radeon AI PRO R9700 review" }).getByRole("link").first();
  await expect(failedLink).toHaveAttribute("href", ITEMS.find((i) => i.id === 3)!.url);
  await expect(failedLink).toHaveAttribute("target", "_blank");
  await expect(failedLink).toHaveAttribute("rel", "noopener noreferrer");
  await page.screenshot({ path: `${SHOTS}/reader-failed-320.png`, fullPage: true });
  const ok = page.locator("[data-news-card]", { hasText: "Qwen" }).getByRole("link").first();
  await expect(ok).toHaveAttribute("href", /\/news\/read\/\d+$/);
  const release = page.locator("[data-news-card]", { hasText: "llama.cpp b9042" }).getByRole("link").first();
  await expect(release).toHaveAttribute("href", "https://example.com/n/2");
  await expect(release).toHaveAttribute("target", "_blank");
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: `${SHOTS}/reader-failed-1280.png`, fullPage: true });
});
