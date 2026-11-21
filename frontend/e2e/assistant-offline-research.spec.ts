/**
 * 目的: AI アシスタントの「この会話は秘密」（会話ごと・一方通行）のボタン・確認・印を守る（PC 1280・モバイル 320）。
 * 押す前に確認が出る（解除できないと書く）→ 押すと入力欄に印が出る・横にはみ出さない・再読み込み後も残る・
 * 解除の操作がどこにも無い。新しい会話を「秘密で始める」切り替えもある。
 * 根拠: 2026-11-21 利用者「途中から秘密の話をしたくなって押すことはよくある。一度押した会話の文脈には秘密が入っているので、
 * 解除して外部へ調査に出られてはいけない」。撮影: E2E_SHOT_DIR（無ければ playwright の出力先）。
 */
import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

for (const [label, width, height] of [["pc-1280", 1280, 800], ["mobile-320", 320, 700]] as const) {
  test(`secret conversation button, confirm and pill (${label})`, async ({ page }, testInfo) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    const shot = async (name: string) => {
      const dir = process.env.E2E_SHOT_DIR || testInfo.outputDir;
      await page.screenshot({ path: `${dir}/secret-conversation-${label}-${name}.png` });
    };
    const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    await page.setViewportSize({ width, height });
    await page.goto("/assistant");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    const create = (confidential: boolean) => page.evaluate(async (c) => {
      const response = await fetch("/api/v1/chat/conversations", {
        method: "POST", credentials: "same-origin", body: JSON.stringify({ confidential: c }),
        headers: { "X-Requested-With": "ControlDeck", "Content-Type": "application/json" },
      });
      return (await response.json()).id as string;
    }, confidential);
    const open = async (id: string) => {
      await page.evaluate((v) => localStorage.setItem("cd-chat-conversation", v), id);
      await page.goto("/assistant");
    };

    const shell = page.locator("[data-assistant-shell]");
    const pill = shell.locator('[data-composer-pill="secret"]');
    const settings = shell.getByRole("button", { name: "AI設定を開く" });

    // 1) ふつうの会話を、途中で秘密にする（確認が出る。キャンセルすれば何も起きない）
    await open(await create(false));
    await expect(pill).toHaveCount(0);
    await settings.click();
    await shell.getByRole("button", { name: "この会話を秘密にする" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("解除できません");
    await shot("1-confirm");
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    await expect(pill).toHaveCount(0);
    await shell.getByRole("button", { name: "この会話を秘密にする" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "秘密にする" }).click();
    await expect(pill).toBeVisible();
    await expect(shell.getByText("秘密の会話", { exact: false }).first()).toBeVisible();
    await expect(shell.getByRole("button", { name: "この会話を秘密にする" })).toHaveCount(0);   // 解除の操作も、押し直しも無い
    await expect(shell.locator("[data-secret-conversation] input, [data-secret-conversation] button")).toHaveCount(0);
    expect(await noOverflow()).toBe(true);
    await shot("2-secret");

    // 2) 再読み込みしても残る（会話に保存）
    await page.reload();
    await expect(pill).toBeVisible();
    expect(await noOverflow()).toBe(true);

    // 3) 新しい会話を最初から秘密で始める切り替え（会話を作る前）
    await page.evaluate(() => localStorage.removeItem("cd-chat-conversation"));
    await page.goto("/assistant");
    await expect(pill).toHaveCount(0);
    await settings.click();
    await shell.getByLabel("秘密の会話として始める").check();
    await expect(pill).toBeVisible();
    await shot("3-start-secret");
  });
}
