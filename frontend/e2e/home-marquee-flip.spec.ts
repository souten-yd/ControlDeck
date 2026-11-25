/** iPhone で「すごい速さでテロップが変わる」不具合の再発防止。ResizeObserver が何度も発火し、Animation の onfinish が早く来る（模擬）条件でも、
 *  1 項目は「流し終わるまでの時間か holdMs の長いほう」より早くめくれない。?marquee=debug の表示も確かめる。
 *  根拠: 2026-11-26 利用者「iPhone でテロップが変わるのがすごい速さ」（?marquee=css・?live=off でも同じ）。 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";
import { mockNews, at } from "./support/news-mock";

const GB = 1024 ** 3;
let tick = 0;
const snapshot = () => ({
  timestamp: new Date().toISOString(),
  cpu: { percent: 20 + (tick % 7), per_cpu: [20, 26], load: [1, 1, 1], freq_mhz: 4200, temperature_c: 54, cores: 2, fan_rpm: 1200, fan_percent: 45 },
  memory: { total: 96 * GB, used: 16 * GB, available: 80 * GB, percent: 17, swap_total: 0, swap_used: 0, swap_percent: 0 },
  gpu: { name: "R9700", utilization_percent: 12 + (tick % 5), vram_used_bytes: GB, vram_total_bytes: 32 * GB, temperature_c: 41, hotspot_c: 45, power_watts: 30, power_cap_watts: 300, fan_rpm: 1280 },
  io: { disk_read_bps: 0, disk_write_bps: 0, net_rx_bps: 0, net_tx_bps: 0 },
  power: { available: false, is_estimate: true, total_watts_estimated: 120, price_per_kwh_yen: 35, psu_efficiency: 0.9 },
  uptime_seconds: 1000,
});
const LONG = "とても長い 1 本目の見出しで、狭い画面では収まらず右から左へ流れていく必要がある文章です。さらにもう少しだけ長くして確実にはみ出させます。";
const BODY = `## 今日の 3 行\n- ${LONG}[1]\n- 短い行はめくるだけ。\n- ${LONG.slice(10)}[1]`;

async function mock(page: Page, body = BODY) {
  await mockNews(page);
  const day = new Date().toLocaleDateString("en-CA");
  const daily = { id: 1, period: "daily", label: "今朝のまとめ", day, body, model: "m", created_at: at(1), error: "", fallback: false, item_ids: [1], sources: [] };
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily, weekly: null }, digests: [daily] } }));
  await page.route(/\/api\/v1\/home-notices$/, (r) => r.fulfill({ json: { total: 1, items: [{ key: "n1", level: "info", title: "お知らせ", message: LONG, to: "/settings", at: 1 }] } }));
  await page.routeWebSocket(/\/system\/metrics\/stream/, (ws) => {
    const send = () => { tick++; ws.send(JSON.stringify(snapshot())); };
    send();
    setInterval(send, 3000);
  });
}


test("ResizeObserver が繰り返し発火し、onfinish が早く来ても、項目は下限より早くめくれない", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  test.setTimeout(100_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await mock(page);
  await page.addInitScript(() => {
    // ResizeObserver: 150ms ごとに必ず発火（幅は同じ）
    const RO = window.ResizeObserver;
    window.ResizeObserver = class extends RO {
      constructor(cb: ResizeObserverCallback) { super(cb); setInterval(() => cb([], this), 150); }
    };
    // onfinish: 設定されて 300ms 後に、まだ終わっていなくても呼ぶ（WebKit で早く来る模擬）
    const desc = Object.getOwnPropertyDescriptor(Animation.prototype, "onfinish")!;
    Object.defineProperty(Animation.prototype, "onfinish", {
      configurable: true,
      get() { return desc.get!.call(this); },
      set(fn) { desc.set!.call(this, fn); if (fn) setTimeout(() => fn.call(this, {}), 300); },
    });
    // enter 層の表示が変わった時刻を集める
    (window as unknown as { __flips: number[] }).__flips = [];
    const start = () => {
      const el = document.querySelector("[data-testid=home-telop] [data-enter]");
      if (!el) return void setTimeout(start, 50);
      let last = el.textContent;
      new MutationObserver(() => { if (el.textContent !== last) { last = el.textContent; (window as unknown as { __flips: number[] }).__flips.push(performance.now()); } }).observe(el, { childList: true, subtree: true, characterData: true });
    };
    start();
  });
  await page.goto("/?marquee=debug");
  await expect(page.getByTestId("marquee-debug").first()).toContainText("rebuild=");
  await page.waitForFunction(() => (window as unknown as { __flips: number[] }).__flips.length >= 3, null, { timeout: 70_000, polling: 500 });
  const flips = await page.evaluate(() => (window as unknown as { __flips: number[] }).__flips);
  const gaps = flips.slice(1).map((t, i) => t - flips[i]);
  console.log("flips", flips.length, "gaps", gaps.map(Math.round));
  expect(flips.length).toBeGreaterThanOrEqual(2);        // めくり自体は起きる（止まっていない）
  for (const g of gaps) expect(g, "項目の見せる時間").toBeGreaterThanOrEqual(3800);
  expect(flips[0]).toBeGreaterThanOrEqual(3800);
  const dbg = await page.getByTestId("marquee-debug").first().textContent();
  expect(dbg).toMatch(/d=\d+ total=\d+ start=\d+ end=\S+ rebuild=\d+ fin=\d+ early=\d+/);
  await page.goto("/settings/appearance");
  await expect(page.getByTestId("debug-flags")).toContainText("?marquee=debug");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate((c) => document.documentElement.classList.toggle("dark", c === "dark"), scheme);
    for (const width of [320, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.getByTestId("debug-flags").scrollIntoViewIfNeeded();
      await page.screenshot({ path: `test-results/home-marquee-flip-${width}-${scheme}.png`, fullPage: true });
    }
  }
  await page.getByRole("button", { name: /解除する/ }).click();
  await page.goto("/");
  await expect(page.locator("[data-testid=home-telop] [data-marquee]")).toBeVisible();
  await expect(page.getByTestId("marquee-debug")).toHaveCount(0);
});
