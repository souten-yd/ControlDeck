/** ホームのテロップを「実機のモバイルに近い条件」（390x844・DPR 3・タッチ・CPU を遅く）で 15 秒測る。API・WebSocket はモック。
 *  目的: 3 秒ごとのメトリクス更新と重なっても、①テロップの DOM が 1 件も作り直されない（mutation 0・animate の付け直し 0）
 *       ②長いコマ（> 33ms）の数と最大が小さい ③transform が流れの途中で巻き戻らない。結果は test-results/home-marquee-mobile-*.json。
 *  根拠: 2026-11-25 利用者「まだ 3 秒ごとっていうのが直ってない気がする」（モバイルでだけカクつく）。 */
import { expect, test, webkit, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
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
const BODY = `## ローカル LLM\n- 傾向: Qwen。[1]\n## 今日の 3 行\n- ${LONG}[1]`;
/** 実データに近い複数行（流れる行 2 本＋収まる行 1 本）。めくりが入る */
const BODY_MULTI = `## 今日の 3 行\n- ${LONG}[1]\n- 短い行はめくるだけ。\n- ${LONG.slice(10)}[1]`;

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

// MARQUEE_BROWSER=webkit で iOS Safari に近い WebKit でも測れる（CPU を遅くする CDP は無いので 1 倍だけ）
const WK = process.env.MARQUEE_BROWSER === "webkit";
for (const [rate, multi] of (WK ? [[1, false], [1, true]] : [[1, false], [4, false], [6, false], [1, true], [6, true]]) as [number, boolean][]) {
  test(`ホームのテロップはモバイル条件（CPU ${rate} 倍遅く${multi ? "・3 行をめくる" : ""}）でも 3 秒ごとにやり直さず・長いコマが少ない`, async ({ browser: chromium }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    test.setTimeout(90_000);
    const browser = WK ? await webkit.launch() : chromium;
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true, baseURL: process.env.CONTROL_DECK_E2E_URL ?? "http://127.0.0.1:8765" });
    const page = await context.newPage();
    await establishSession(page, context);
    await page.addInitScript(() => {
      const w = window as unknown as { __anim: number };
      w.__anim = 0;
      const orig = Element.prototype.animate;
      Element.prototype.animate = function (this: Element, ...a: Parameters<Element["animate"]>) {
        if (this.hasAttribute("data-inner")) w.__anim++;
        return orig.apply(this, a);
      };
    });
    await mock(page, multi ? BODY_MULTI : BODY);
    if (rate > 1) await (await context.newCDPSession(page)).send("Emulation.setCPUThrottlingRate", { rate });
    if (process.env.MARQUEE_TRACE && !WK) await browser.startTracing(page, { path: "test-results/marquee-trace.json", categories: ["devtools.timeline", "cc", "blink.animations"] });
    await page.goto("/");
    const telop = "[data-testid=home-telop] [data-inner]";
    await expect(page.locator(telop)).toBeVisible();
    await expect(page.locator(telop).locator("xpath=..")).toHaveAttribute("data-marquee", "run");
    await page.waitForTimeout(2000);
    const r = await page.evaluate((ms) => new Promise<Record<string, number>>((resolve) => {
      const w = window as unknown as { __anim: number };
      const anim0 = w.__anim;
      const roots = [...document.querySelectorAll("[data-testid=home-telop] [data-testid=marquee], [data-testid=home-notice] [data-testid=marquee]")];
      let mutations = 0;
      const mo = new MutationObserver((l) => { mutations += l.length; });
      roots.forEach((el) => mo.observe(el, { subtree: true, childList: true, attributes: true, characterData: true }));
      let docMutations = 0;
      const mo2 = new MutationObserver((l) => { docMutations += l.length; });
      mo2.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      let longTasks = 0, longMax = 0;
      new PerformanceObserver((l) => l.getEntries().forEach((e) => { longTasks++; longMax = Math.max(longMax, e.duration); })).observe({ entryTypes: ["longtask"] });
      const el = document.querySelector<HTMLElement>("[data-testid=home-telop] [data-inner]")!;
      const t0 = performance.now();
      let prev = t0, xPrev = 0, frames = 0, over33 = 0, over50 = 0, maxGap = 0, rewinds = 0;
      const loop = (t: number) => {
        const gap = t - prev; prev = t; frames++;
        if (frames > 1) { maxGap = Math.max(maxGap, gap); if (gap > 33.4) over33++; if (gap > 50) over50++; }
        const x = new DOMMatrix(getComputedStyle(el).transform).m41;
        if (x > xPrev + 1e-6) rewinds++;
        xPrev = x;
        if (t - t0 < ms) requestAnimationFrame(loop);
        else { mo.disconnect(); mo2.disconnect(); resolve({ docMutations, svgs: document.querySelectorAll("svg").length, frames, fps: Math.round((frames * 1000) / (t - t0)), over33, over50, maxGap: Math.round(maxGap), longTasks, longMax: Math.round(longMax), mutations, reanimated: w.__anim - anim0, rewinds }); }
      };
      requestAnimationFrame(loop);
    }), 15_000);
    const out = { engine: WK ? "webkit" : "chromium", rate, multi, ...r, metricsTicks: tick };
    writeFileSync(`test-results/home-marquee-mobile-${WK ? "webkit-" : ""}${rate}x${multi ? "-multi" : ""}.json`, JSON.stringify(out));
    console.log(`marquee-mobile ${rate}x:`, JSON.stringify(out));
    if (!multi) {           // 1 行だけなら、3 秒ごとの更新でテロップは 1 度も作り直されない
      expect(r.mutations, "テロップの DOM が作り直された").toBe(0);
      expect(r.reanimated, "animation が付け直された").toBe(0);
    }
    if (process.env.MARQUEE_TRACE && !WK) await browser.stopTracing();
    await context.close();
    if (WK) await browser.close();
  });
}
