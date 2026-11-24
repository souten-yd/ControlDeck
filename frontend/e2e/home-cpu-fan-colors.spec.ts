// 目的: ホームのモバイルのカードで、(1) 利用率と RAM・VRAM の色が違う（computed style・ライト/ダーク）、
//   (2) RAM・VRAM の容量がグラフの右下に重なる、(3) CPU のカードの補足の行に CPU ファンの回転数と PWM が出る
//   （API はモック）、(4) どの幅でも横にはみ出さない。撮影: test-results/home-fan-colors-{390,320,1280}[-dark].png。
// 根拠: 2026-11-24 利用者「CPU のところに回転数の表示を追加して・RAM の容量はグラフの右下に・利用率と VRAM 使用率の色が同じなので変えて」。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const GB = 1024 ** 3;
const snapshot = (fan: boolean) => ({
  timestamp: new Date().toISOString(),
  cpu: { percent: 23, per_cpu: [20, 26], load: [1, 1, 1], freq_mhz: 4200, temperature_c: 54, cores: 2,
    fan_rpm: fan ? 1200 : null, fan_percent: fan ? 45 : null },
  memory: { total: 96 * GB, used: 16 * GB, available: 80 * GB, percent: 17, swap_total: 0, swap_used: 0, swap_percent: 0 },
  gpu: { name: "R9700", utilization_percent: 12, vram_used_bytes: 1 * GB, vram_total_bytes: 32 * GB, temperature_c: 41,
    hotspot_c: 45, power_watts: 30, power_cap_watts: 300, fan_rpm: 1280 },
  io: { disk_read_bps: 0, disk_write_bps: 0, net_rx_bps: 0, net_tx_bps: 0 },
  power: { available: false, is_estimate: true, total_watts_estimated: 120, price_per_kwh_yen: 35, psu_efficiency: 0.9 },
  uptime_seconds: 1000,
});

async function mockStream(page: Page, fan: boolean) {
  await page.routeWebSocket(/\/system\/metrics\/stream/, (ws) => {
    const send = () => ws.send(JSON.stringify(snapshot(fan)));
    send();
    setInterval(send, 400);
  });
}

const colorOf = (loc: ReturnType<Page["locator"]>) => loc.evaluate((el) => getComputedStyle(el).color);
const noOverflow = (page: Page) =>
  expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

for (const [width, height] of [[390, 844], [320, 700], [1280, 900]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`home cpu fan, colors, capacity (${width}px, ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await mockStream(page, true);
      await page.goto("/");
      const suffix = `${width}${scheme === "dark" ? "-dark" : ""}`;
      if (width < 768) {
        const dual = page.getByTestId("metrics-dual");
        await expect(dual).toBeVisible();
        const cpuTile = dual.locator("> div").nth(0);
        const gpuTile = dual.locator("> div").nth(1);
        // 見えている文字（innerText）で比べる。320px では「rpm」「PWM」を省く
        const shown = (tile: typeof cpuTile) => tile.locator("span.text-sky-500").evaluate((el) => (el as HTMLElement).innerText.replace(/\s+/g, " "));
        expect(await shown(cpuTile)).toBe(width >= 360 ? "✻1200 rpm・PWM 45%" : "✻1200・45%");
        expect(await shown(gpuTile)).toBe(width >= 360 ? "✻1280 rpm" : "✻1280");
        // 温度も切れない（補足の行が横にはみ出さない）
        for (const tile of [cpuTile, gpuTile]) {
          expect(await tile.locator("p.num").evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
        }
        // 利用率の数字の色が RAM・VRAM と違う
        for (const tile of [cpuTile, gpuTile]) {
          const [a, b] = await Promise.all([
            colorOf(tile.locator("span.num").nth(0)),
            colorOf(tile.locator("span.num").nth(1)),
          ]);
          expect(a).not.toBe(b);
        }
        // 容量はグラフの枠の右下
        for (const [tile, text] of [[cpuTile, "16/96GB"], [gpuTile, "1/32GB"]] as const) {
          const cap = tile.getByTestId("capacity");
          await expect(cap).toHaveText(text);
          const box = (await cap.boundingBox())!;
          const frame = (await tile.locator("div.relative").boundingBox())!;
          expect(box.x + box.width).toBeGreaterThan(frame.x + frame.width - 8);
          expect(box.y + box.height).toBeGreaterThan(frame.y + frame.height - 8);
          expect(box.y).toBeGreaterThanOrEqual(frame.y);
        }
        // 補足の行に容量は無い（グラフ側へ移した）
        await expect(cpuTile.locator("p")).not.toContainText("GB");
      } else {
        await expect(page.getByTestId("metrics-dual")).toBeHidden();
        const summary = page.getByRole("region", { name: "システムサマリー" });
        await expect(summary.locator("span.flex").filter({ hasText: /1200\s*RPM.*45%/ }).first()).toBeVisible();
      }
      await noOverflow(page);
      await page.waitForTimeout(900);
      await page.screenshot({ path: `test-results/home-fan-colors-${suffix}.png` });
      await page.screenshot({ path: `/tmp/claude-1000/-data1tb-ControlDeck/71a33d52-f7f0-4607-8f6d-577e292874d3/scratchpad/shots5/home-fan-colors-${suffix}.png` });
    });
  }
}

test("home without a readable CPU fan shows nothing for it (390px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await mockStream(page, false);
  await page.goto("/");
  const cpuTile = page.getByTestId("metrics-dual").locator("> div").nth(0);
  await expect(cpuTile).toContainText("54°C");
  await expect(cpuTile).not.toContainText("✻");
  await expect(page.getByText(/error|エラー/i)).toHaveCount(0);
});
