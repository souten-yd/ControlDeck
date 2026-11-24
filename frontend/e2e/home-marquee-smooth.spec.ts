/** ホームの流れる文字（ニュースのテロップ・通知の 1 行）が、3 秒ごとのメトリクス更新の間も滑らかに流れ続ける。API・WebSocket はモック。
 *  目的: ①10 秒の間 rAF で transform の X を毎フレーム集め、流れている間は巻き戻らない ②3 秒ごとの境目で 50ms 以上の止まりが無い
 *       ③1 秒あたりのフレームが 50 以上（headless の目安）。計測の値は test-results/home-marquee-smooth.json に残す。
 *  根拠: 2026-11-24 利用者「ホーム画面が更新が 3 秒ごとになっている気がする。ニュースの右から左に移動する文字は 60fps くらい滑らかに。
 *       ブラウザ側で一度情報を受信したらスムーズに動かせないか」。 */
import { expect, test, type Page } from "@playwright/test";
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

async function mock(page: Page) {
  await mockNews(page);
  const day = new Date().toLocaleDateString("en-CA");
  const daily = { id: 1, period: "daily", label: "今朝のまとめ", day, body: BODY, model: "m", created_at: at(1), error: "", fallback: false, item_ids: [1], sources: [] };
  await page.route(/\/api\/v1\/news\/digests$/, (r) => r.fulfill({ json: { latest: { daily, weekly: null }, digests: [daily] } }));
  await page.route(/\/api\/v1\/home-notices$/, (r) => r.fulfill({ json: { total: 1, items: [{ key: "n1", level: "info", title: "お知らせ", message: LONG, to: "/settings", at: 1 }] } }));
  await page.routeWebSocket(/\/system\/metrics\/stream/, (ws) => {
    const send = () => { tick++; ws.send(JSON.stringify(snapshot())); };
    send();
    setInterval(send, 3000);       // 本番と同じ 3 秒ごと
  });
}

/** 10 秒間、rAF ごとに inner の transform X を集める。 */
async function sample(page: Page, selector: string, ms = 10_000) {
  return page.evaluate(({ selector, ms }) => new Promise<{ t: number; x: number }[]>((resolve) => {
    const el = document.querySelector<HTMLElement>(selector)!;
    const out: { t: number; x: number }[] = [];
    const t0 = performance.now();
    const loop = (t: number) => {
      out.push({ t, x: new DOMMatrix(getComputedStyle(el).transform).m41 });
      if (t - t0 < ms) requestAnimationFrame(loop); else resolve(out);
    };
    requestAnimationFrame(loop);
  }), { selector, ms });
}

/** 流れている間（X が変わり続ける一続き）の最大の止まり ms、巻き戻りの回数、1 秒あたりのフレーム数。 */
function analyse(s: { t: number; x: number }[]) {
  const changes: { t: number; x: number }[] = [];
  let last = s[0];
  for (const p of s) if (Math.abs(p.x - last.x) > 1e-6) { changes.push(p); last = p; }
  let maxStall = 0, rewinds = 0, moved = 0;
  for (let i = 1; i < changes.length; i++) {
    if (changes[i].x > changes[i - 1].x) { rewinds++; continue; }         // 次の項目・ループで 0 へ戻る（走りの切れ目）
    maxStall = Math.max(maxStall, changes[i].t - changes[i - 1].t);
    moved++;
  }
  const fps = (s.length - 1) / ((s[s.length - 1].t - s[0].t) / 1000);
  return { maxStall: Math.round(maxStall), rewinds, moved, fps: Math.round(fps) };
}

for (const [name, selector, throttle] of [
  ["telop", "[data-testid=home-telop] [data-inner]", 1], ["notice", "[data-testid=home-notice] [data-inner]", 1],
  ["telop-cpu6x", "[data-testid=home-telop] [data-inner]", 6],        // 弱い端末（CPU を 6 倍遅く）でも主スレッドの止まりが小さい
] as const) {
  test(`ホームの流れる文字（${name}）は 3 秒ごとの更新の間も止まらない`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    test.setTimeout(60_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await establishSession(page, context);
    // 流れの animation を付け直した回数と、メトリクスが届いた回数を数える（付け直しが止まりの原因になる）
    await page.addInitScript(() => {
      const w = window as unknown as { __anim: number; __renders: number };
      w.__anim = 0;
      const orig = Element.prototype.animate;
      Element.prototype.animate = function (this: Element, ...a: Parameters<Element["animate"]>) {
        if (this.hasAttribute("data-inner")) w.__anim++;
        return orig.apply(this, a);
      };
    });
    await mock(page);
    if (throttle > 1) await (await context.newCDPSession(page)).send("Emulation.setCPUThrottlingRate", { rate: throttle });
    await page.goto("/");
    await expect(page.locator(selector)).toBeVisible();
    await expect(page.locator(selector).locator("xpath=..")).toHaveAttribute("data-marquee", "run");
    await page.waitForTimeout(1500);        // 最初の止まり（pauseMs 1 秒）が過ぎてから測る
    const animBefore = await page.evaluate(() => (window as unknown as { __anim: number }).__anim);
    const cpuBefore = await page.getByText(/^\d+%$/).first().textContent();
    const s = await sample(page, selector);
    const animAfter = await page.evaluate(() => (window as unknown as { __anim: number }).__anim);
    const r = { ...analyse(s), reanimated: animAfter - animBefore, metricsTicks: tick, cpuBefore, cpuAfter: await page.getByText(/^\d+%$/).first().textContent() };
    writeFileSync(`test-results/home-marquee-smooth-${name}.json`, JSON.stringify(r));
    console.log(`marquee ${name}:`, JSON.stringify(r));
    expect(r.moved).toBeGreaterThan(100);       // 実際に流れていた
    expect(r.maxStall).toBeLessThan(throttle > 1 ? 100 : 50);
    if (throttle === 1) expect(r.fps).toBeGreaterThanOrEqual(50);
    expect(r.reanimated).toBe(0);           // 3 秒ごとの描き直しで animation を付け直さない
  });
}
