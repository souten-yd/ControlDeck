import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** ニュース: 取得先の管理（/news/sources）。一覧・絞り込み・詳細と履歴・通信確認・間隔の変更・有効/無効・削除・一括・目録（おすすめをまとめて足す）・フィードを作る手伝い・サイトを追加。API は page.route で差し替える。
 *  根拠: 2026-11-24 利用者「設定の中にボタンを置き、別の管理ページに飛ばして、疎通確認だったりとか、これまでの取得実績、最終取得日時などを確認、サイトごとに確認できたりとか、新しいサイトの追加、削除などができるように」
 *  「これらを元に、情報収集の蓄積、ナレッジ化を効率的に実施してほしい」（目録・分野ごとの取得の間隔・フィードを作る手伝い）。
 *  撮影は test-results/news-sources-{list,detail,add}-{320,1280}-{light,dark}.png。 */

const at = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString().replace(/\.\d+Z$/, "Z");
type Src = Record<string, unknown> & { key: string; kind: string; value: string; name: string; enabled: boolean; interval: number };
const mk = (o: Partial<Src> & { key: string; kind: string; value: string; name: string }): Src => ({
  format: o.kind === "github" ? "github" : "rss", domain: "example.com", enabled: true, interval: 0, groups: ["ai-official"], last_at: at(0.2), last_ok_at: at(0.2), last_ok: true,
  last_error: "", last_count: 12, last_status: 200, last_skipped: "", week: [100, 100, 90, 100, 100, 100, 100], total: 240, ...o,
});
const START = (): Src[] => [
  mk({ key: "rss:https://good.example/feed.xml", kind: "rss", value: "https://good.example/feed.xml", name: "良いフィード", domain: "good.example", interval: 30 }),
  mk({ key: "rss:https://bad.example/feed.xml", kind: "rss", value: "https://bad.example/feed.xml", name: "失敗中のフィード", domain: "bad.example", last_ok: false, last_error: "HTTP 403", last_status: 403, last_count: 0, last_ok_at: at(30), week: [100, 60, 20, 0, 0, null, 0], total: 8 }),
  mk({ key: "github:ollama/ollama", kind: "github", value: "ollama/ollama", name: "GitHub ollama/ollama", domain: "github.com", interval: 15, groups: ["gh-llm"], total: 500 }),
  mk({ key: "rss:https://old.example/feed.xml", kind: "rss", value: "https://old.example/feed.xml", name: "止まったフィード", domain: "old.example", last_ok_at: at(24 * 20), week: [50, 50, 50, 50, 50, 50, 50] }),
  mk({ key: "reddit:LocalLLaMA", kind: "reddit", value: "LocalLLaMA", name: "r/LocalLLaMA", format: "reddit", domain: "reddit.com", enabled: false, last_at: "", last_ok_at: "", last_ok: null, week: [null, null, null, null, null, null, null], total: 0 }),
];
const OK = { ok: true, count: 25, titles: ["一つ目の記事", "二つ目の記事"], ms: 420, status: 200, latest: "2026-11-24", error: null };
const BOT = { ok: false, count: 0, titles: [], ms: 180, status: 403, latest: "", error: { code: "blocked", message: "取れません（ロボット確認）: HTTP 403", status: 403, location: "" } };
const PRESETS = {
  verified: "2026-11-24", excluded: [{ group: "ai-official", name: "Qwen公式ブログ", value: "https://qwenlm.github.io/blog/index.xml", reason: "止まっている（最終 2025-09-22）" }],
  groups: [{ id: "ai-official", label: "AI・公式", hint: "公式ブログ", count: 2 }, { id: "gh-llm", label: "GitHub・LLM推論", hint: "Release", count: 2 }],
  items: [
    { id: "rss:https://openai.com/news/rss.xml", group: "ai-official", also: [], kind: "rss", name: "OpenAI", value: "https://openai.com/news/rss.xml", lang: "en", note: "", key: false, verified: "2026-11-24", interval: 30, recommended: true, latest: "2026-11-23", count: 20 },
    { id: "rss:https://aws.example/feed", group: "ai-official", also: [], kind: "rss", name: "AWS ML", value: "https://aws.example/feed", lang: "en", note: "", key: false, verified: "2026-11-24", interval: 30, recommended: false },
    { id: "github:vllm-project/vllm", group: "gh-llm", also: [], kind: "github", name: "vLLM", value: "vllm-project/vllm", lang: "en", note: "Release", key: false, verified: "2026-11-24", interval: 15, recommended: true },
    { id: "github:ollama/ollama", group: "gh-llm", also: [], kind: "github", name: "Ollama", value: "ollama/ollama", lang: "en", note: "Release", key: false, verified: "2026-11-24", interval: 15, recommended: true },
  ],
};

async function mock(page: Page) {
  const st = { items: START(), bulk: [] as { action: string; keys: string[]; interval?: number }[], added: [] as { kind: string; value: string; name?: string; interval?: number }[], checks: [] as { kind: string; value: string }[], generated: [] as unknown[] };
  const list = () => ({ items: st.items, groups: PRESETS.groups, default_interval: 30, reddit_key: false, limit: 500, interval_range: [5, 10080], running: false,
    formats: { rss: "RSS/Atom", page: "一覧の巡回", github: "GitHub", reddit: "Reddit", steam: "Steam", arxiv: "arXiv", search: "検索", discourse: "フォーラム", codeberg: "Codeberg", hackernews: "Hacker News", huggingface: "Hugging Face" } });
  await page.route(/\/api\/v1\/news\/sources\/manage$/, (r) => r.fulfill({ json: list() }));
  await page.route(/\/api\/v1\/news\/sources\/manage\/detail\?.*/, (r) => {
    const key = new URL(r.request().url()).searchParams.get("key");
    const item = st.items.find((i) => i.key === key);
    return r.fulfill({ json: { item, history: [
      { at: at(0.2), ok: true, status: 200, count: 12, new: 3, ms: 840, error: "", skipped: "" },
      { at: at(0.7), ok: false, status: 403, count: 0, new: 0, ms: 300, error: "HTTP 403", skipped: "" },
      { at: at(1.2), ok: true, status: 200, count: 12, new: 0, ms: 910, error: "", skipped: "" }],
      articles: [{ id: 1, title: "最近の記事のタイトル", url: "https://good.example/1", published_at: at(2) }] } });
  });
  await page.route(/\/api\/v1\/news\/sources\/manage\/bulk$/, (r) => {
    const b = r.request().postDataJSON();
    st.bulk.push(b);
    if (b.action === "check") return r.fulfill({ json: { results: b.keys.map((key: string) => ({ key, ...(key.includes("bad.") ? BOT : OK) })) } });
    st.items = st.items.flatMap((i) => {
      if (!b.keys.includes(i.key)) return [i];
      if (b.action === "delete") return [];
      if (b.action === "enable") return [{ ...i, enabled: true }];
      if (b.action === "disable") return [{ ...i, enabled: false }];
      return [{ ...i, interval: b.interval }];
    });
    return r.fulfill({ json: { count: b.keys.length } });
  });
  await page.route(/\/api\/v1\/news\/sources\/manage\/add$/, (r) => {
    const body = r.request().postDataJSON();
    for (const it of body.items) { st.added.push(it); if (!st.items.some((i) => i.key === `${it.kind}:${it.value}`)) st.items.push(mk({ key: `${it.kind}:${it.value}`, kind: it.kind, value: it.value, name: it.name ?? it.value, interval: it.interval ?? 0, last_at: "", last_ok: null, last_ok_at: "", total: 0 })); }
    return r.fulfill({ json: { added: body.items.length } });
  });
  await page.route(/\/api\/v1\/news\/sources\/generate$/, (r) => {
    const b = r.request().postDataJSON();
    st.generated.push(b);
    if (b.type === "github" && !String(b.target).includes("/")) return r.fulfill({ status: 422, json: { detail: "owner/repo の形（例 ggml-org/llama.cpp）か GitHub の URL で入れてください" } });
    if (b.type === "steam") return r.fulfill({ json: { kind: "rss", value: "https://store.steampowered.com/feeds/news/app/427520/", name: "Steam 427520 のニュース", lang: "en" } });
    return r.fulfill({ json: { kind: "rss", value: "https://news.google.com/rss/search?q=ROCm%20LLM&hl=ja&gl=JP&ceid=JP:ja", name: "Google ニュース: ROCm LLM（日本語）", lang: "ja" } });
  });
  await page.route(/\/api\/v1\/news\/sources\/check$/, (r) => {
    const b = r.request().postDataJSON();
    st.checks.push(b);
    return r.fulfill({ json: String(b.value).includes("bad.") ? BOT : OK });
  });
  await page.route(/\/api\/v1\/news\/sources\/discover$/, (r) => r.fulfill({ json: { url: "https://site.example/", title: "テストのサイト", mode: "feeds", page: null,
    feeds: [{ url: "https://site.example/feed.xml", count: 20, latest: "2026-11-24", titles: ["RSS の記事 1"] }], error: null } }));
  await page.route(/\/api\/v1\/news\/sources$/, (r) => r.fulfill({ json: { config: { enabled: { news: true }, github: [], codeberg: [], rss: [], pages: [], hackernews: false, huggingface: false, discourse: [], reddit: [] },
    kinds: { news: "ニュース" }, reddit_key: false, limit: 500, sources: [], last: { finished: at(0.2), new: 0 }, running: false } }));
  await page.route(/\/api\/v1\/news\/presets$/, (r) => r.fulfill({ json: PRESETS }));
  return st;
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("取得先の管理: 一覧・絞り込み・詳細と履歴・通信確認・間隔と有効/無効・削除・一括", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const [width, scheme] of [[1280, "light"], [320, "light"], [1280, "dark"], [320, "dark"]] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: scheme });
    const st = await mock(page);
    if (width === 1280 && scheme === "light") await establishSession(page, context);
    await page.goto("/news/sources");
    await expect(page.getByTestId("news-src-row")).toHaveCount(5);
    await expect(page.getByTestId("news-sources-summary")).toContainText("失敗中 1");
    await expect(page.getByTestId("news-sources-summary")).toContainText("止まっている 1");
    // 状態の表示と 7 日の棒
    await expect(page.getByTestId("news-src-row").filter({ hasText: "失敗中のフィード" }).getByTestId("news-src-state")).toHaveText("失敗中");
    await expect(page.getByTestId("news-week-bars").first()).toHaveAttribute("aria-label", /成功率/);
    expect(await noOverflow(page)).toBe(true);
    await page.screenshot({ path: `test-results/news-sources-list-${width}-${scheme}.png`, fullPage: true });
    // 絞り込み: 状態・検索・並べ替え
    await page.getByTestId("news-src-status").selectOption("failing");
    await expect(page.getByTestId("news-src-row")).toHaveCount(1);
    await page.getByTestId("news-src-status").selectOption("");
    await page.getByTestId("news-src-search").fill("ollama");
    await expect(page.getByTestId("news-src-row")).toHaveCount(1);
    await page.getByTestId("news-src-search").fill("");
    await page.getByTestId("news-src-group").selectOption("gh-llm");
    await expect(page.getByTestId("news-src-row")).toHaveCount(1);
    await page.getByTestId("news-src-group").selectOption("");
    await page.getByTestId("news-src-sort").selectOption("fail");
    await expect(page.getByTestId("news-src-row").first()).toContainText("失敗中のフィード");
    // 詳細: 履歴・記事・通信確認
    await page.getByTestId("news-src-row").first().getByTestId("news-src-open").click();
    const detail = page.getByTestId("news-source-detail");
    await expect(detail.getByTestId("news-history").locator("tbody tr")).toHaveCount(3);
    await expect(detail).toContainText("失敗: HTTP 403");
    await expect(page.getByTestId("news-detail-articles")).toContainText("最近の記事のタイトル");
    await page.getByTestId("news-detail-check").click();
    await expect(page.getByTestId("news-detail-check-result")).toContainText("取れません（ロボット確認）");
    expect(st.checks[0]).toMatchObject({ kind: "rss", value: "https://bad.example/feed.xml" });
    expect(await noOverflow(page)).toBe(true);
    await page.screenshot({ path: `test-results/news-sources-detail-${width}-${scheme}.png`, fullPage: true });
    // 間隔の変更・有効/無効
    await detail.getByLabel("失敗中のフィード の取得の間隔").selectOption("360");
    await expect.poll(() => st.bulk.find((b) => b.action === "interval")).toMatchObject({ keys: ["rss:https://bad.example/feed.xml"], interval: 360 });
    await page.getByTestId("news-detail-toggle").click();
    await expect.poll(() => st.bulk.some((b) => b.action === "disable")).toBe(true);
    // 削除（確認つき）
    await page.getByTestId("news-detail-delete").click();
    await page.getByTestId("news-detail-delete-confirm").click();
    await expect(page.getByTestId("news-src-row")).toHaveCount(4);
    // 一括: 選んで通信確認・無効・削除
    await page.getByTestId("news-pick").nth(0).click();
    await page.getByTestId("news-pick").nth(1).click();
    await expect(page.getByTestId("news-picked")).toHaveText("2 件を選択中");
    await page.getByTestId("news-bulk-check").click();
    await expect(page.getByTestId("news-src-check-result").first()).toBeVisible();
    await page.getByTestId("news-bulk-interval").click();
    await expect.poll(() => st.bulk.filter((b) => b.action === "interval").length).toBe(2);
    await expect(page.getByTestId("news-picked")).toHaveText("0 件を選択中");     // 操作の後は選択を外す
    await page.getByTestId("news-pick").nth(0).click();
    await page.getByTestId("news-pick").nth(1).click();
    page.once("dialog", (d) => void d.accept());
    await page.getByTestId("news-bulk-delete").click();
    await expect(page.getByTestId("news-src-row")).toHaveCount(2);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});

test("取得先の管理: おすすめをまとめて足す・分野で足す・フィードを作る手伝い（通信確認つき）・サイトを追加", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  for (const [width, scheme] of [[1280, "light"], [320, "light"], [1280, "dark"], [320, "dark"]] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: scheme });
    const st = await mock(page);
    if (width === 1280 && scheme === "light") await establishSession(page, context);
    await page.goto("/news/sources");
    await page.getByTestId("news-tab-add").click();
    const panel = page.getByTestId("news-presets");
    // おすすめをまとめて足す（すでにある ollama は足さない）
    await expect(page.getByTestId("news-preset-add-recommended")).toContainText("2 件");
    await page.getByTestId("news-preset-add-recommended").click();
    await expect.poll(() => st.added.map((a) => a.value).sort()).toEqual(["https://openai.com/news/rss.xml", "vllm-project/vllm"]);
    expect(st.added.find((a) => a.value === "vllm-project/vllm")?.interval).toBe(15);        // 目録の既定の間隔が付く
    expect(st.added.find((a) => a.value.includes("openai"))?.interval).toBe(30);
    // 分野を選んで足す
    await page.getByTestId("news-preset-group").selectOption("ai-official");
    await expect(panel.getByRole("checkbox", { name: /OpenAI/ })).toBeChecked();
    await panel.getByRole("checkbox", { name: /AWS ML/ }).click();
    await expect.poll(() => st.added.some((a) => a.value === "https://aws.example/feed")).toBe(true);
    await panel.getByRole("button", { name: /入れなかったもの/ }).click();
    await expect(page.getByTestId("news-preset-excluded")).toContainText("止まっている（最終 2025-09-22）");
    // フィードを作る: 失敗の入力は理由を出す → Steam を作って通信確認 → 足す
    const maker = page.getByTestId("news-feed-maker");
    await page.getByTestId("news-maker-github").click();
    await page.getByTestId("news-maker-target").fill("bad");
    await page.getByTestId("news-maker-make").click();
    await expect(page.getByTestId("news-maker-error")).toContainText("owner/repo");
    await page.getByTestId("news-maker-steam").click();
    await page.getByTestId("news-maker-target").fill("427520");
    await page.getByTestId("news-maker-make").click();
    await expect(page.getByTestId("news-maker-check")).toContainText("取れました: 25 件");
    expect(st.checks.at(-1)).toMatchObject({ kind: "rss", value: "https://store.steampowered.com/feeds/news/app/427520/" });
    await page.getByTestId("news-maker-add").click();
    await expect.poll(() => st.added.some((a) => a.value.includes("app/427520"))).toBe(true);
    await page.getByTestId("news-maker-keyword").click();
    await maker.getByLabel("キーワード").fill("ROCm LLM");
    await page.getByTestId("news-maker-make").click();
    await expect(page.getByTestId("news-maker-result")).toContainText("Google ニュース: ROCm LLM");
    expect(await noOverflow(page)).toBe(true);
    await page.screenshot({ path: `test-results/news-sources-add-${width}-${scheme}.png`, fullPage: true });
    // サイトを追加（RSS の発見）
    await page.getByTestId("news-site-url").fill("https://site.example/");
    await page.getByTestId("news-site-discover").click();
    await expect(page.getByTestId("news-site-candidate")).toHaveCount(1);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});

test("設定 → ニュース: ソースは要点と「取得先を管理」への入口だけ", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 320, height: 900 });
  await mock(page);
  await establishSession(page, context);
  await page.goto("/settings/news");
  await expect(page.getByTestId("news-sources-brief")).toContainText("取得先 5 件");
  await expect(page.getByTestId("news-sources-brief")).toContainText("失敗中 1");
  await page.getByTestId("news-manage-link").click();
  await expect(page).toHaveURL(/\/news\/sources$/);
  expect(await noOverflow(page)).toBe(true);
});
