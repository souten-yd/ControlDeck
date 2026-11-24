import { expect, test, type BrowserContext, type CDPSession, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";
import { ITEMS, mockNews } from "./support/news-mock";

/** ニュースの画面の「指でずらす」操作・流れる文字・上の部分。API は page.route でモック（外へ通信しない）。
 *  目的: ①ON AIR のチャンネルを左右の指でずらして変えられる（少しだけなら戻る・縦ならスクロールでチャンネルは変わらない）
 *       ②（まとめはバブルに変えたので news-digest-bubbles.spec.ts へ移した）③Marquee がデータの読み直し（項目の配列が新しく作られる）で位置を巻き戻さない
 *       ④上の部分（取り直す・設定・表示・検索）が押せて、狭い幅でも低い。撮影は test-results/ と scratchpad の shots4 に残す。
 *  根拠: 2026-11-24 利用者「CH の上で左右にフリックすると CH が変わるようにして」「今週のまとめの上で左右にフリックすると今朝のまとめ」
 *       「流れる文字・フリップのアニメーションはスムーズに」「取り直すボタンがある行から今週のまとめまでの表示がやぼったい」。 */

const SHOTS = process.env.NEWS_SHOTS_DIR ?? "test-results";

/** 本物の指（touch）の入力を CDP で送る。pointerType=touch の pointer イベントになる。 */
class Finger {
  constructor(private cdp: CDPSession) {}
  private send(type: string, x?: number, y?: number) {
    return this.cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x: x!, y: y! }] });
  }
  async down(x: number, y: number) { await this.send("touchStart", x, y); }
  async move(x: number, y: number) { await this.send("touchMove", x, y); }
  async up() { await this.send("touchEnd"); }
  /** from → to へ steps 回に分けて動かす。release=false なら離さない。dt は 1 歩ごとの待ち（速さ）。 */
  async drag(from: { x: number; y: number }, to: { x: number; y: number }, opts: { steps?: number; dt?: number; release?: boolean } = {}) {
    const { steps = 8, dt = 16, release = true } = opts;
    await this.down(from.x, from.y);
    for (let i = 1; i <= steps; i++) {
      await this.move(from.x + ((to.x - from.x) * i) / steps, from.y + ((to.y - from.y) * i) / steps);
      await new Promise((r) => setTimeout(r, dt));
    }
    if (release) await this.up();
  }
}

/** 要素の上端から offset px の所で、x を a → b へずらす（毎回測り直す: 切り替えで中身の高さが変わるので、前の位置では別の要素に乗ってしまう）。 */
async function swipeAt(page: Page, finger: Finger, testId: string, a: number, b: number, opts: { steps?: number; dt?: number; release?: boolean } = {}, offset = 80) {
  await page.getByTestId(testId).evaluate((el) => Promise.all(el.getAnimations({ subtree: false }).map((x) => x.finished.catch(() => null))));   // 前の切り替えの動きが終わるのを待つ
  await page.waitForTimeout(450);
  const c = await center(page, testId, offset);
  await finger.drag({ x: c.x + a, y: c.y }, { x: c.x + b, y: c.y }, opts);
}

async function setup(page: Page, context: BrowserContext, width: number, opts: { design?: string; height?: number } = {}) {
  await page.setViewportSize({ width, height: opts.height ?? 844 });
  const mocks = await mockNews(page, { design: opts.design });
  await establishSession(page, context);
  await page.goto("/news");
  const cdp = await context.newCDPSession(page);
  return { mocks, finger: new Finger(cdp) };
}
/** 上端から offset px の所（中身の高さが変わっても同じ要素の上に指が乗る位置）。 */
const center = async (page: Page, testId: string, offset = 80) => {
  const b = (await page.getByTestId(testId).boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + Math.min(b.height / 2, offset), w: b.width };
};
const pressed = (page: Page, name: string) => page.getByRole("group", { name: "チャンネルの切り替え" }).getByRole("button", { name, exact: true });

test("ニュース: ON AIR を左右に指でずらすとチャンネルが変わり、少しなら戻り、縦ならスクロールでチャンネルは変わらない", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  const { finger } = await setup(page, context, 390);
  await expect(page.getByTestId("onair-swipe")).toBeVisible();
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  const c = await center(page, "onair-swipe");

  // 指に付いて動く: 途中（離さない）で ON AIR の中身（モニター・本文）だけがずれ、見出しの行・まとめ・CH のボタンの列は動かない
  const still = ["news-bar", "digests"].map((id) => page.getByTestId(id));
  const boxes = async () => Promise.all([...still.map((l) => l.boundingBox()), page.getByRole("group", { name: "チャンネルの切り替え" }).boundingBox()]);
  const before = await boxes();
  await swipeAt(page, finger, "onair-swipe", +40, -30, { release: false });
  const tx = (sel: string) => page.locator(sel).first().evaluate((el) => new DOMMatrix(getComputedStyle(el).transform).m41);
  expect(await tx(".nw-monitor")).toBeLessThan(-50);
  expect(await tx(".nw-onair-copy")).toBeLessThan(-50);
  expect(await boxes()).toEqual(before);
  expect(await page.getByTestId("onair-swipe").evaluate((el) => el.style.transform)).toBe("");
  await page.screenshot({ path: `${SHOTS}/swipe-mid-390.png` });
  await finger.up();                                   // 70px は幅 342 の 25%（85px）未満・遅い → 元へ戻る
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => page.locator(".nw-monitor").evaluate((el) => (el as HTMLElement).style.transform)).toBe("");

  // 大きくずらして離す → 次の CH 01、もう一度 → CH 02、右へずらす → CH 01 へ戻る
  await swipeAt(page, finger, "onair-swipe", +120, -120, { dt: 25 });
  await expect(pressed(page, "CH 01")).toHaveAttribute("aria-pressed", "true");
  await swipeAt(page, finger, "onair-swipe", +120, -120, { dt: 25 });
  await expect(pressed(page, "CH 02")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("onair-channel")).toContainText("CH 02");
  await swipeAt(page, finger, "onair-swipe", -120, +120, { dt: 25 });
  await expect(pressed(page, "CH 01")).toHaveAttribute("aria-pressed", "true");
  // 短くても速くはじく（フリック）と切り替わる
  await swipeAt(page, finger, "onair-swipe", +40, -40, { steps: 3, dt: 6 });
  await expect(pressed(page, "CH 02")).toHaveAttribute("aria-pressed", "true");
  // ボタンも今まで通り
  await pressed(page, "ALL").click();
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  // 端（ALL）で右へずらしても何も起きない
  await swipeAt(page, finger, "onair-swipe", -120, +120, { dt: 25 });
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  // 縦にずらす: スクロールはブラウザに任せ、チャンネルは変わらない
  const c3 = await center(page, "onair-swipe");
  await finger.drag({ x: c3.x, y: c3.y + 100 }, { x: c3.x + 10, y: c3.y - 80 }, { dt: 25 });
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator(".nw-monitor").evaluate((el) => (el as HTMLElement).style.transform)).toBe("");
  // 画面の端（20px 以内）からはチャンネルではなくアプリのメニューを引く操作（チャンネルは変わらない）
  const c2 = await center(page, "onair-swipe");
  await finger.drag({ x: 6, y: c2.y }, { x: 306, y: c2.y }, { dt: 25 });
  await expect(pressed(page, "ALL")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-nav-drawer] [role='dialog']")).toBeVisible();
});

test("ニュース: 新着の一覧を左右に指でずらすと前後のチャンネルになる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  const { finger } = await setup(page, context, 390);
  await page.getByRole("tab", { name: "新着" }).click();
  const group = page.getByRole("group", { name: "チャンネル" });
  await expect(group.getByRole("button", { name: "全チャンネル" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("list-swipe")).toBeVisible();
  await swipeAt(page, finger, "list-swipe", +120, -120, { dt: 25 });
  await expect(group.getByRole("button", { name: /CH 01/ })).toHaveAttribute("aria-pressed", "true");
});

test("ニュース: Marquee は項目の配列が作り直されても（読み直し）位置を巻き戻さず、要素も作り直さない", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.clock.install();
  await mockNews(page);
  const long = (n: number) => `長い見出し ${n}: ` + "ローカル LLM の新しい動きを追いかける記事のタイトルがとても長く続く".repeat(5);
  let fetches = 0;
  const rows = [1, 2, 3].map((n) => ({ ...ITEMS[0], id: 900 + n, title: long(n), channel: null }));
  await page.route(/\/api\/v1\/news\/items\?.*/, (r) => { fetches++; return r.fulfill({ json: { items: rows.map((x) => ({ ...x })), next: null } }); });   // 毎回新しいオブジェクト
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily: null, weekly: null }, digests: [] } }));
  await page.route(/\/api\/v1\/news\/settings$/, (r) => r.fulfill({ json: { settings: { feed_refresh_minutes: 1, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" },
    quiet: { enabled: false, start: "01:00", end: "05:00" }, llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto", digest_focus: "", prep_enabled: true, prep_max_per_run: 100 },
    rag: { enabled: true, fulltext: false, on_prune: "keep" }, imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 } }, weekdays: [], interval_minutes: 30 } }));
  await establishSession(page, context);
  await page.goto("/");
  const marquee = page.getByTestId("home-telop").getByTestId("marquee");
  await expect(marquee.locator("[data-marquee='run']")).toBeVisible();
  const inner = marquee.locator("[data-inner]").first();
  await inner.evaluate((el) => { (el as HTMLElement & { __mark?: number }).__mark = 1; });
  const x = () => inner.evaluate((el) => new DOMMatrix(getComputedStyle(el).transform).m41);
  await expect.poll(x, { timeout: 15_000 }).toBeLessThan(-60);            // 最初の止まり（1 秒）を過ぎて流れている
  const before = await x();
  const calls = fetches;
  await page.clock.fastForward(61_000);                                      // 1 分の読み直しを起こす
  await expect.poll(() => fetches, { timeout: 10_000 }).toBeGreaterThan(calls);
  await page.waitForTimeout(300);
  const after = await x();
  expect(after).toBeLessThanOrEqual(before + 1);                             // 巻き戻っていない（0 に戻らない）
  expect(after).toBeLessThan(-60);
  expect(await inner.evaluate((el) => (el as HTMLElement & { __mark?: number }).__mark)).toBe(1);   // 要素は作り直されていない
  // 動かすのは transform（translate3d）だけ
  expect(await inner.evaluate((el) => getComputedStyle(el).willChange)).toBe("transform");
});

for (const design of ["broadcast", "newspaper"]) {
  for (const [width, height] of [[390, 844], [320, 700], [1280, 900]] as const) {
    test(`ニュース: 上の部分（取り直す・設定・表示・検索）は押せて低い（${design} ${width}px）`, async ({ page, context }) => {
      test.skip(!hasSession(), "ログイン情報がありません");
      const { mocks } = await setup(page, context, width, { design, height });
      const bar = page.getByTestId("news-bar");
      await expect(bar).toBeVisible();
      const digests = page.getByTestId("digests");
      await expect(digests).toBeVisible();
      const top = (await bar.boundingBox())!, dig = (await digests.boundingBox())!;
      const span = Math.round(dig.y - top.y);
      console.log(`SPAN ${design} ${width}px: bar ${Math.round(top.height)}px / digests bottom ${Math.round(dig.y + dig.height - top.y)}px`);
      expect(top.height).toBeLessThanOrEqual(width < 900 ? 90 : 60);
      // 取り直す・設定はアイコンだけ（名前は aria-label）
      await expect(bar.getByRole("button", { name: "取り直す" })).toBeVisible();
      await expect(bar.getByRole("link", { name: "設定", exact: true })).toHaveAttribute("href", "/settings/news");
      // 撮影（初期の状態: ライト・ダーク）
      for (const scheme of ["light", "dark"] as const) {
        await page.emulateMedia({ colorScheme: scheme });
        await page.evaluate((s) => document.documentElement.classList.toggle("dark", s === "dark"), scheme);
        await page.screenshot({ path: `${SHOTS}/bar-${design}-${width}-${scheme}.png` });
      }
      // 表示（下線のタブ）
      await bar.getByRole("tab", { name: "新着" }).click();
      await expect(bar.getByRole("tab", { name: "新着" })).toHaveAttribute("aria-selected", "true");
      await bar.getByRole("tab", { name: "放送" }).click();
      // 検索: 1 段（窓の中に 全文 / 意味）
      if (width < 900) await bar.getByRole("button", { name: "検索を開く" }).click();
      const box = bar.getByRole("searchbox", { name: "アーカイブ全体を検索" });
      await expect(box).toBeVisible();
      await expect(bar.getByRole("button", { name: "全文", exact: true })).toHaveAttribute("aria-pressed", "true");
      await bar.getByRole("button", { name: "意味で探す" }).click();
      await expect(bar.getByRole("button", { name: "意味で探す" })).toHaveAttribute("aria-pressed", "true");
      await box.fill("Qwen");
      await expect(page.getByRole("heading", { name: "「Qwen」の意味での検索結果" })).toBeVisible();
      await box.fill("");
      if (width < 900) await bar.getByRole("button", { name: "検索を閉じる" }).click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.evaluate(() => { for (const el of Array.from(document.querySelectorAll("*"))) if (el.scrollTop) el.scrollTop = 0; });
    });
  }
}
