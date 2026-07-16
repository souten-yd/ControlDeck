import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("assistant input never expands beyond a 320px viewport", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/assistant");

  const dialog = page.getByRole("dialog");
  const textarea = dialog.getByPlaceholder("メッセージを入力...");
  await expect(textarea).toBeVisible();
  await textarea.fill("mobile-overflow-" + "abcdefghijklmnopqrstuvwxyz0123456789".repeat(30));

  const layout = await page.evaluate(() => {
    const input = document.querySelector<HTMLTextAreaElement>('textarea[placeholder="メッセージを入力..."]')!;
    const row = input.parentElement!;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
    const rect = (element: Element) => element.getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      dialog: rect(dialog).toJSON(),
      row: rect(row).toJSON(),
      input: rect(input).toJSON(),
      inputScrollWidth: input.scrollWidth,
      inputFontSize: Number.parseFloat(getComputedStyle(input).fontSize),
      inputMinWidth: getComputedStyle(input).minWidth,
    };
  });
  console.log("ASSISTANT_MOBILE_LAYOUT", JSON.stringify(layout));
  expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.dialog.right).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.row.right).toBeLessThanOrEqual(layout.viewportWidth - 15);
  expect(layout.input.right).toBeLessThanOrEqual(layout.viewportWidth - 69);
  expect(layout.inputFontSize).toBeGreaterThanOrEqual(16);
  expect(layout.inputMinWidth).toBe("0px");

  await page.setViewportSize({ width: 1280, height: 800 });
  const desktopLayout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    dialogRight: document.querySelector<HTMLElement>('[role="dialog"]')!.getBoundingClientRect().right,
  }));
  console.log("ASSISTANT_DESKTOP_LAYOUT", JSON.stringify(desktopLayout));
  expect(desktopLayout.documentScrollWidth).toBeLessThanOrEqual(desktopLayout.viewportWidth);
  expect(desktopLayout.bodyScrollWidth).toBeLessThanOrEqual(desktopLayout.viewportWidth);
  expect(desktopLayout.dialogRight).toBeLessThanOrEqual(desktopLayout.viewportWidth);
});
