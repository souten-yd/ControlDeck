// 目的: モバイル（390 / 320px）のホームを詰め、状態のカード群が 1 画面に収まること。
//   - CPU+RAM・GPU+VRAM がそれぞれ 1 枚（2 本の線）で、横 1 段。履歴の選択の行（履歴・生データ・n 点）は無い。
//   - 電力の 4 項目（PSU 総出力・今回の起動中・今日・今月）が 1 行。
//   - LLM カードは見出しの行に「ローカル · 未ロード」。
//   - 一番上に通知の 1 行（最も重要な 1 件の札・本文・ほか n 件）。色だけでなく札の文字がある。PC（1280px）にも出る。
//   - どの幅でも横にはみ出さない。撮影: test-results/home-compact-{390,320,1280}.png（ライト）と -dark.png。
// 根拠: 2026-11-23 利用者「モバイルのホーム画面は 1 画面で今の情報とニュースを表示したい…履歴の 1 行は不要…
//   最終的な通知の内容を一行で表示して、重要度で題名と色を変えて」。API は模擬する（metrics は実体）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const notices = {
  total: 3,
  items: [
    { key: "app:1", level: "error", title: "SearXNG が失敗しています", message: "ログを確認してください", to: "/logs?app=1&stream=stderr", at: 3 },
    { key: "alert:2", level: "warning", title: "GPU 温度", message: "GPU 温度が高い", to: "/settings/notifications", at: 2 },
    { key: "approval:3", level: "attention", title: "承認待ち", message: "PR を承認してください", to: "/?approvals=1", at: 1 },
  ],
};

async function mock(page: Page) {
  await page.route("**/api/v1/home-notices", (route) => route.fulfill({ json: notices }));
  await page.route("**/api/v1/models/llama/capacity", (route) => route.fulfill({ json: {
    endpoints: [], failed: [], power: { enabled: true, avg_w: 410, yen_per_hour: 17.2, today_yen: 52.3, today_seconds: 5400 },
    last_local: { running_alias: "Qwen3-Coder-Next", prefill_tokens_per_second: 1450, tokens_per_second: 51.2, ctx_used: 12000, ctx_total: 131072 },
  } }));
  await page.route("**/api/v1/models/cloud", (route) => route.fulfill({ json: {
    providers: [{ id: "openrouter", configured: true, enabled: true }], egress: {}, caller_labels: {} } }));
  await page.route("**/api/v1/models/cloud-summary", (route) => route.fulfill({ json: {
    running: [], today: { cost_usd: 0.42, calls: 12 }, month: { cost_usd: 3.1, calls: 90 },
    last: { provider: "openrouter", model: "deepseek/deepseek-v4-flash", at: 1, ttft_s: 1.2, tokens_per_second: 80.4, prompt_tokens: 8000, completion_tokens: 900, context_length: 128000 } } }));
  await page.route("**/api/v1/models/cloud/openrouter/account", (route) => route.fulfill({ json: { supported: true, balance: 18.5 } }));
}

const noOverflow = (page: Page) =>
  expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

for (const [width, height] of [[390, 844], [320, 700], [1280, 900]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`home compact (${width}px, ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await mock(page);
      await page.goto("/");
      const notice = page.getByTestId("home-notice");
      await expect(notice).toBeVisible();
      await expect(notice).toHaveAttribute("data-level", "error");
      await expect(notice).toContainText("エラー");           // 色だけに頼らず札の文字がある
      await expect(notice).toContainText("1/3");
      await expect(page.locator("#metrics-range")).toHaveCount(0);   // 履歴の選択は設定へ移した
      await expect(page.getByText(/生データ|\d+点/)).toHaveCount(0);

      const llm = page.getByRole("region", { name: "LLM の利用状況" });
      await expect(llm).toBeVisible();
      if (width < 768) {
        const dual = page.getByTestId("metrics-dual");
        await expect(dual).toBeVisible();
        await expect(dual.getByText("CPU", { exact: true })).toBeVisible();
        await expect(dual.getByText("RAM", { exact: true })).toBeVisible();
        await expect(dual.getByText("GPU", { exact: true })).toBeVisible();
        await expect(dual.getByText("VRAM", { exact: true })).toBeVisible();
        const [a, b] = await dual.locator("> div").evaluateAll((els) => els.map((e) => e.getBoundingClientRect().top));
        expect(Math.abs(a - b)).toBeLessThan(2);                 // 横 1 段
        await expect(llm.getByText("ローカル", { exact: true })).toBeVisible();
        if (width === 390) {
          // 状態のカード群が 1 画面に入る（ニュースの先頭はその下に続く）
          const bottom = await llm.evaluate((el) => el.getBoundingClientRect().bottom);
          expect(bottom).toBeLessThan(height);
        }
        // 電力の 4 項目が 1 行（同じ高さ）
        const tops = await Promise.all(["PSU 総出力", "今回の起動中", "今日", "今月"].map((t) =>
          page.getByText(t, { exact: true }).first().evaluate((el) => Math.round(el.getBoundingClientRect().top))));
        expect(new Set(tops).size).toBe(1);
      } else {
        await expect(page.getByTestId("metrics-dual")).toBeHidden();
      }
      await noOverflow(page);
      await page.waitForTimeout(600);
      await page.screenshot({ path: `test-results/home2-compact-${width}${scheme === "dark" ? "-dark" : ""}.png` });
    });
  }
}

// 目的: 通知は未解決の全件を 1 件ずつ回す。収まらない本文は右から左へ流れ（札・位置・× は固定）、流し切ったら縦にめくって
//   次の件へ（札の重要度も切り替わる）。収まる件は流れず数秒見せてからめくる。1 件だけなら めくらない。
// 根拠: 2026-11-23 利用者「流したら、次フリップして別の情報を流す。情報のあるだけ流す。一つしかなければ同じものをずっと」。
test("home notice scrolls and flips (390px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await mock(page);
  const long = "ログを確認してください。".repeat(8);
  const items = [{ ...notices.items[0], message: long }, ...notices.items.slice(1)];
  await page.route("**/api/v1/home-notices", (route) => route.fulfill({ json: { total: 3, items } }));
  await page.goto("/");
  const notice = page.getByTestId("home-notice");
  const badge = notice.getByText("エラー");
  await expect(notice.locator("[data-marquee]")).toHaveAttribute("data-marquee", "run");
  const x0 = (await badge.boundingBox())!.x;
  await page.waitForTimeout(2600);                      // 端の 1 秒停止のあと流れ始める
  const tx = await notice.locator("[data-inner]").evaluate((el) => new DOMMatrix(getComputedStyle(el).transform).m41);
  expect(tx).toBeLessThan(-10);
  expect((await badge.boundingBox())!.x).toBe(x0);      // 札は固定
  await page.screenshot({ path: "test-results/home2-notice-scroll-390.png" });
  // 流し切るとめくって次の件（警告）へ。めくる途中も撮る。
  await page.waitForFunction(() => document.querySelector("[data-testid=marquee]")!
    .getAnimations({ subtree: true }).some((a) => (a.effect as KeyframeEffect).getTiming().duration === 350), null, { timeout: 30000 });
  await page.screenshot({ path: "test-results/home2-notice-flip-390.png" });
  await expect(notice).toHaveAttribute("data-level", "warning");
  await expect(page.getByTestId("home-notice-pos")).toHaveText("2/3");
});

test("home notice with a single item does not flip (390px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  await mock(page);
  await page.route("**/api/v1/home-notices", (route) => route.fulfill({ json: { total: 1, items: [{ ...notices.items[0], message: "" }] } }));
  await page.goto("/");
  await expect(page.getByTestId("home-notice").locator("[data-marquee]")).toHaveAttribute("data-marquee", "fit");
  await expect(page.getByTestId("home-notice-pos")).toHaveCount(0);
  await page.waitForTimeout(4800);
  await expect(page.getByTestId("home-notice")).toHaveAttribute("data-level", "error");
});
