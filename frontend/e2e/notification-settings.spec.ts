// 目的:
// - 設定 → 通知 の「通知の種類」で、種類ごとのスイッチの保存（再読み込みしても残る）と、既定（調査の完了は有効・
//   ふつうの会話は無効）が出る。
// - 「お休みモード」の開始・終了の時刻が保存され、表示に出る。今の状態（お休み中かどうか）が出る。
// - 320px と 1280px で横にあふれず、ライト・ダークで読める（撮影を test-results に残す）。
// 根拠: 2026-11-23 利用者の依頼（お休みモード・通知の種類の拡充）。実の API（検証用インスタンス）で確かめる。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test("notification kinds and quiet hours are saved and shown", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await page.goto("/settings/notifications");
  const kinds = page.locator("[data-notification-kinds]");
  const quiet = page.locator("[data-quiet-hours]");
  await expect(kinds).toBeVisible();

  // 既定
  await expect(kinds.locator("[data-kind-toggle='code_done']")).toHaveAttribute("aria-checked", "true");
  await expect(kinds.locator("[data-kind-toggle='chat_done']")).toHaveAttribute("aria-checked", "false");
  await expect(quiet.locator("[data-quiet-start]")).toHaveValue("22:00");
  await expect(quiet.locator("[data-quiet-end]")).toHaveValue("07:00");

  // 種類を切る・お休みの時刻を変える。再読み込みしても残る
  await kinds.locator("[data-kind-toggle='code_done']").click();
  await expect(kinds.locator("[data-kind-toggle='code_done']")).toHaveAttribute("aria-checked", "false");
  const put = page.waitForResponse((r) => r.url().endsWith("/notification-settings") && r.request().method() === "PUT");
  await quiet.locator("[data-quiet-start]").fill("23:30");
  await put;
  await page.reload();
  await expect(page.locator("[data-kind-toggle='code_done']")).toHaveAttribute("aria-checked", "false");
  await expect(page.locator("[data-quiet-start]")).toHaveValue("23:30");
  await expect(page.locator("[data-quiet-state]")).toBeVisible();

  // 今の状態: 24 時間ぶんの範囲にして、お休み中の表示を確かめる
  await page.locator("[data-quiet-start]").fill("00:00");
  await page.locator("[data-quiet-end]").fill("23:59");
  await expect(page.locator("[data-quiet-state='active']")).toContainText("今はお休み中です（23:59 まで）");

  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate((s) => document.documentElement.classList.toggle("dark", s === "dark"), scheme);
    for (const width of [320, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.locator("[data-quiet-hours]").scrollIntoViewIfNeeded();
      await page.screenshot({ path: `test-results/notification-settings-${width}-${scheme}.png`, fullPage: true });
    }
  }

  // 後片付け（検証用インスタンスの既定へ戻す）
  await page.evaluate(async () => {
    const body = JSON.stringify({ kinds: { code_done: true }, quiet: { start: "22:00", end: "07:00" } });
    await fetch("/api/v1/notification-settings", { method: "PUT", body, headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" } });
  });
});
