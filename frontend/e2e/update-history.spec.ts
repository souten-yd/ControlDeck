// 目的: 本体の自動更新が動いたことに気づけること。何から何へ更新するのかを Release の名前で出すこと。
//       - 設定 → 更新とバックアップに履歴（時刻・自動/手動・版・成否）と、待っている理由が出る
//       - 自動で更新した後、次に画面を開いたら一度だけ知らせ、もう一度開いても出さない
//       320px でも横にはみ出さない。
// 根拠: 2026-11-20 利用者「自動更新が動いていない気がする。気づいてないだけ？」。実際は自動 38 回動いていたが、
//       通知先が無く画面にも出ないので気づけなかった。API は模擬する。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const now = Math.floor(Date.now() / 1000);
const history = { items: [
  { phase: "succeeded", automatic: true, release_tag: "ControlDeck_v0.97", new_sha: "c".repeat(40), finished_at: now - 600 },
  { phase: "failed", automatic: true, release_tag: "ControlDeck_v0.96", error: "更新後の画面が配信されません", finished_at: now - 7200 },
  { phase: "succeeded", automatic: false, release_tag: "ControlDeck_v0.95", new_sha: "b".repeat(40), finished_at: now - 9000 },
] };

for (const width of [320, 1280]) {
  test(`update history and one-time auto update notice (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/system\/control-deck-update\/history$/, (route) => route.fulfill({ json: history }));
    await page.route(/\/api\/v1\/system\/control-deck-update$/, (route) => route.fulfill({ json: {
      channel: "release", current_sha: "c".repeat(40), remote_sha: "d".repeat(40), release_tag: "ControlDeck_v0.98",
      current_release_tag: "ControlDeck_v0.97",
      update_available: true, can_update: true, error: "", job: {} } }));
    await page.route(/\/api\/v1\/system\/control-deck-update\/auto$/, (route) => route.fulfill({ json: {
      enabled: true, idle_minutes: 5, check_interval_minutes: 30, busy: ["LLM の推論中"] } }));
    await page.addInitScript(() => { if (!sessionStorage.getItem("cleared")) { localStorage.removeItem("control-deck-auto-update-seen"); sessionStorage.setItem("cleared", "1"); } });

    await page.goto("/");
    await expect(page.getByText("ControlDeck を v0.97 に自動で更新しました")).toBeVisible();
    await page.reload();
    await page.waitForTimeout(1500);
    await expect(page.getByText("ControlDeck を v0.97 に自動で更新しました")).toHaveCount(0);   // 一度だけ

    await page.goto("/settings/system");
    const list = page.locator("[data-update-history]");
    await expect(list.locator("li")).toHaveCount(3);
    await expect(list.locator("li").nth(0)).toContainText("自動");
    await expect(list.locator("li").nth(0)).toContainText("v0.97");
    await expect(list.locator("li").nth(0)).toContainText("成功");
    await expect(list.locator("li").nth(1)).toContainText("失敗: 更新後の画面が配信されません");
    await expect(list.locator("li").nth(2)).toContainText("手動");
    await expect(page.locator("[data-auto-update-wait]")).toContainText("v0.98 の適用を待っています: LLM の推論中");
    // 何から何へ更新するのかを Release の名前で出す（コミットの羅列だけにしない）
    await expect(page.locator("[data-update-current]")).toContainText("いまの版: v0.97");
    await expect(page.locator("[data-update-latest]")).toContainText("最新の正式 Release: v0.98");
    await expect(page.locator("[data-update-latest]")).toContainText("v0.97 → v0.98");
    await page.getByRole("button", { name: "本体を更新" }).click();
    await expect(page.getByText("v0.97 から v0.98 へ更新し")).toBeVisible();
    await page.getByRole("button", { name: "キャンセル" }).click();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/update-history-${width}.png`, fullPage: true });
  });
}
