/** ホームの 3 秒ごとの更新（メトリクス）があっても、テロップ・通知の一行の部品は描き直されない。API・WebSocket はモック。
 *  目的: モバイル（390x844・CPU 4 倍遅く）で 15 秒の間、①テロップ・通知・ホームの殻の React の描き直しが 0 回 ②テロップの DOM が変わらない
 *       ③流れの animation を付け直さない ④いっぽうメトリクスの部品は 3 秒ごとに描き直される（計測が効いている証拠）。
 *  根拠: 2026-11-25 利用者「3 秒ごとぐらいに更新されている。画面の更新は 60fps、データの取得は波形は 3 秒ごと、ニュースは更新のときに」。
 *  数え方: 部品の本体で countRender（window.__renders）、DOM は MutationObserver。結果は test-results/home-marquee-decouple.json。 */
import { expect, test } from "@playwright/test";
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
  uptime_seconds: 1000 + tick,
});
const LONG = "とても長い 1 本目の見出しで、狭い画面では収まらず右から左へ流れていく必要がある文章です。さらにもう少しだけ長くして確実にはみ出させます。";

test("3 秒ごとの更新があってもテロップ・通知の部品は描き直されない（モバイル・CPU 4 倍遅く・15 秒）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await page.addInitScript(() => {
    const w = window as unknown as { __anim: number; __dom: Record<string, number> };
    w.__anim = 0; w.__dom = {};
    const orig = Element.prototype.animate;
    Element.prototype.animate = function (this: Element, ...a: Parameters<Element["animate"]>) {
      if (this.hasAttribute("data-inner")) w.__anim++;
      return orig.apply(this, a);
    };
    // どの部分の DOM が変わったか（属性・文字・子の増減）を領域ごとに数える
    addEventListener("DOMContentLoaded", () => {
      new MutationObserver((list) => {
        for (const m of list) {
          const el = (m.target.nodeType === 1 ? m.target : m.target.parentElement) as Element | null;
          const area = el?.closest("[data-testid=home-telop]") ? "telop" : el?.closest("[data-testid=home-notice]") ? "notice"
            : el?.closest("[aria-label=システムサマリー]") ? "metrics" : el?.closest("[data-news-home-card]") ? "news-card" : "other";
          w.__dom[area] = (w.__dom[area] ?? 0) + 1;
        }
      }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
    });
  });
  await mockNews(page);
  const day = new Date().toLocaleDateString("en-CA");
  const daily = { id: 1, period: "daily", label: "今朝のまとめ", day, body: `## 今日の 3 行\n- ${LONG}[1]`, model: "m", created_at: at(1), error: "", fallback: false, item_ids: [1], sources: [] };
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily, weekly: null }, digests: [daily] } }));
  await page.route(/\/api\/v1\/home-notices$/, (r) => r.fulfill({ json: { total: 1, items: [{ key: "n1", level: "info", title: "お知らせ", message: LONG, to: "/settings", at: 1 }] } }));
  await page.routeWebSocket(/\/system\/metrics\/stream/, (ws) => {
    const send = () => { tick++; ws.send(JSON.stringify(snapshot())); };
    send();
    setInterval(send, 3000);
  });
  await (await context.newCDPSession(page)).send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.goto("/");
  await expect(page.locator("[data-testid=home-telop] [data-inner]")).toBeVisible();
  await expect(page.locator("[data-testid=home-notice] [data-inner]")).toBeVisible();
  await page.waitForTimeout(1500);
  const snap = () => page.evaluate(() => {
    const w = window as unknown as { __renders?: Record<string, number>; __anim: number; __dom: Record<string, number> };
    return { renders: { ...(w.__renders ?? {}) }, anim: w.__anim, dom: { ...w.__dom } };
  });
  const before = await snap(), ticks0 = tick;
  await page.waitForTimeout(15_000);
  const after = await snap();
  const diff = (a: Record<string, number>, b: Record<string, number>) =>
    Object.fromEntries([...new Set([...Object.keys(a), ...Object.keys(b)])].map((k) => [k, (b[k] ?? 0) - (a[k] ?? 0)]));
  const r = { metricsTicks: tick - ticks0, renders: diff(before.renders, after.renders), dom: diff(before.dom, after.dom), reanimated: after.anim - before.anim };
  writeFileSync("test-results/home-marquee-decouple.json", JSON.stringify(r, null, 1));
  console.log("decouple:", JSON.stringify(r));
  expect(r.metricsTicks).toBeGreaterThanOrEqual(4);
  expect(r.renders.HomeMetrics ?? 0).toBeGreaterThanOrEqual(4);        // メトリクスの部品は 3 秒ごとに描き直される（計測が効いている）
  for (const name of ["Marquee", "NewsTelop", "HomeNoticeLine", "DashboardPage"]) expect(r.renders[name] ?? 0, `${name} の描き直し`).toBe(0);
  expect(r.dom.telop ?? 0).toBe(0);
  expect(r.dom.notice ?? 0).toBe(0);
  expect(r.dom["news-card"] ?? 0).toBe(0);
  expect(r.reanimated).toBe(0);
});
