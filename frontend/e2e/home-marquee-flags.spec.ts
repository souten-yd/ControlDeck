/** 切り分け用の目印（?marquee=css・?live=off）の E2E。下は計測の spec と同じモックを使う。
 *  元の説明: ホームのテロップを「実機のモバイルに近い条件」（390x844・DPR 3・タッチ・CPU を遅く）で 15 秒測る。API・WebSocket はモック。
 *  目的: 3 秒ごとのメトリクス更新と重なっても、①テロップの DOM が 1 件も作り直されない（mutation 0・animate の付け直し 0）
 *       ②長いコマ（> 33ms）の数と最大が小さい ③transform が流れの途中で巻き戻らない。結果は test-results/home-marquee-mobile-*.json。
 *  根拠: 2026-11-25 利用者「まだ 3 秒ごとっていうのが直ってない気がする」（モバイルでだけカクつく）。 */
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

const kind = (page: Page) => page.evaluate(() => document.querySelector("[data-testid=home-telop] [data-inner]")!.getAnimations()[0]?.constructor.name);
const cpuText = (page: Page) => page.getByText(/^\d+%$/).first().textContent();

test("目印なしは WAAPI のまま。?marquee=css で CSS の animation になり、?live=off で 3 秒ごとの更新が止まり、設定から解除できる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await mock(page);
  await page.goto("/");
  await expect(page.locator("[data-testid=home-telop] [data-marquee]")).toHaveAttribute("data-marquee", "run");
  expect(await kind(page)).toBe("Animation");                       // 既定は WAAPI
  const base = await cpuText(page);
  await page.waitForTimeout(7000);
  expect(await cpuText(page)).not.toBe(base);                       // 既定はメトリクスが更新される
  await page.goto("/?marquee=css&live=off");
  await expect(page.locator("[data-testid=home-telop] [data-marquee]")).toHaveAttribute("data-marquee", "run");
  expect(await kind(page)).toBe("CSSAnimation");
  const x0 = await page.evaluate(() => new DOMMatrix(getComputedStyle(document.querySelector("[data-testid=home-telop] [data-inner]")!).transform).m41);
  const first = await cpuText(page);
  await page.waitForTimeout(7000);
  expect(await cpuText(page)).toBe(first);                          // live=off は最初の 1 点のあと変わらない
  const x1 = await page.evaluate(() => new DOMMatrix(getComputedStyle(document.querySelector("[data-testid=home-telop] [data-inner]")!).transform).m41);
  expect(x1).not.toBe(x0);                                          // CSS でも流れている
  await page.goto("/settings/appearance");
  await expect(page.getByTestId("debug-flags")).toContainText("?marquee=css");
  await expect(page.getByTestId("debug-flags")).toContainText("?live=off");
  await page.getByRole("button", { name: /解除する/ }).click();
  await page.goto("/");
  await expect(page.locator("[data-testid=home-telop] [data-marquee]")).toHaveAttribute("data-marquee", "run");
  expect(await kind(page)).toBe("Animation");                       // 解除で元に戻る
});
