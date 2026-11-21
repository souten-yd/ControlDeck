/**
 * 目的: 設定のリモートデスクトップの切り替えで、ナビの「Remote」が消え、/remote に無効の表示が出て、
 * オンで戻ること（PC 幅 1280 と 320px）。画面の撮影も残す。
 * 根拠: 利用者の依頼（2026-11-21）「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。
 * オフになったらホームのアイコンも消す」。検証用インスタンスは xrdp を操作しない（toggle.py の isolated）。
 */
import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const shots = process.env.REMOTE_TOGGLE_SHOTS ?? "test-results/remote-toggle";

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function openMenu(page: Page, width: number) {
  if (width < 768) await page.getByRole("button", { name: "メニューを開く" }).click();
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 320, height: 700 }]) {
  test(`remote desktop toggle hides and restores the feature at ${viewport.width}px`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize(viewport);
    await login(page);
    const remoteLink = () => page.getByRole("link", { name: "Remote", exact: true });

    await page.goto("/settings/features");
    const toggle = page.getByRole("switch", { name: "リモートデスクトップを有効にする" });
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(page.getByText("オフにすると、この PC のリモートデスクトップのサーバー（xrdp）を止め")).toBeVisible();
    await page.screenshot({ path: `${shots}/settings-on-${viewport.width}.png` });

    await openMenu(page, viewport.width);
    await expect(remoteLink().first()).toBeVisible();
    await page.keyboard.press("Escape");
    if (viewport.width < 768) await page.mouse.click(viewport.width - 5, viewport.height / 2);

    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `${shots}/settings-off-${viewport.width}.png` });
    await openMenu(page, viewport.width);
    await expect(remoteLink()).toHaveCount(0);
    await page.screenshot({ path: `${shots}/menu-off-${viewport.width}.png` });

    await page.goto("/remote");
    await expect(page.getByText("リモートデスクトップは無効です（設定で有効にできます）")).toBeVisible();
    await page.screenshot({ path: `${shots}/remote-off-${viewport.width}.png` });
    const blocked = await page.request.get("/api/v1/remote/connections");
    expect(blocked.status()).toBe(403);

    await page.goto("/settings/features");
    await page.getByRole("switch", { name: "リモートデスクトップを有効にする" }).click();
    await expect(page.getByRole("switch", { name: "リモートデスクトップを有効にする" })).toHaveAttribute("aria-checked", "true");
    await openMenu(page, viewport.width);
    await expect(remoteLink().first()).toBeVisible();
    await page.screenshot({ path: `${shots}/menu-on-${viewport.width}.png` });
    await page.goto("/remote");
    await expect(page.getByText("リモートデスクトップは無効です")).toHaveCount(0);
  });
}
