import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("assistant input never expands beyond a 320px viewport", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      configurable: true,
      value: async () => {
        const context = new AudioContext();
        const destination = context.createMediaStreamDestination();
        (window as Window & { __assistantTestAudio?: AudioContext }).__assistantTestAudio = context;
        return destination.stream;
      },
    });
  });
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0; // 未認証状態での初回 /auth/me 401 はログイン画面の正常な判定
  await page.goto("/assistant");
  const conversationId = await page.evaluate(async () => {
    const response = await fetch("/api/v1/chat/conversations", {
      method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
    });
    return (await response.json()).id as string;
  });
  await page.evaluate((id) => localStorage.setItem("cd-chat-conversation", id), conversationId);
  await page.reload();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "AIアシスタントを閉じる" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "選択中の会話を削除" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  await expect(dialog.getByLabel("処理モード")).toHaveValue("auto");
  const textarea = dialog.locator("textarea");
  await expect(textarea).toBeVisible();
  await textarea.fill("最新のGPUニュースを検索して");
  await expect(dialog.getByLabel("現在の機能")).toHaveText("自動判定: Web検索");
  await dialog.getByLabel("処理モード").selectOption("academic");
  await expect(dialog.getByLabel("現在の機能")).toHaveText("選択: 学術検索");
  await dialog.getByLabel("処理モード").selectOption("auto");
  await textarea.fill("20ノードのワークフローを作って");
  await expect(dialog.getByLabel("現在の機能")).toHaveText("自動判定: フロー生成");
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
      mic: rect(document.querySelector('[aria-label="音声で入力"]')!).toJSON(),
      close: rect(document.querySelector('[aria-label="AIアシスタントを閉じる"]')!).toJSON(),
      trash: rect(document.querySelector('[aria-label="選択中の会話を削除"]')!).toJSON(),
      mode: rect(document.querySelector('[aria-label="処理モード"]')!).toJSON(),
      history: rect(document.querySelector('[aria-label="会話を切替"]')!).toJSON(),
      inputScrollWidth: input.scrollWidth,
      inputFontSize: Number.parseFloat(getComputedStyle(input).fontSize),
      inputMinWidth: getComputedStyle(input).minWidth,
    };
  });
  console.log("ASSISTANT_MOBILE_LAYOUT", JSON.stringify(layout));
  expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.dialog.right).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.row.right).toBeLessThanOrEqual(layout.viewportWidth - 12);
  expect(layout.input.right).toBeLessThanOrEqual(layout.viewportWidth - 69);
  expect(layout.mic.width).toBeGreaterThanOrEqual(44);
  expect(layout.close.width).toBeGreaterThanOrEqual(44);
  expect(layout.trash.width).toBeGreaterThanOrEqual(44);
  expect(layout.mode.right).toBeLessThanOrEqual(layout.history.left);
  expect(layout.history.width).toBeGreaterThanOrEqual(60);
  expect(layout.history.width).toBeLessThan(140);
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

  // 選択中の履歴は確認ダイアログなしで削除され、直ちに新規会話へ切り替わる。
  await dialog.getByRole("button", { name: "選択中の会話を削除" }).click();
  await expect(dialog.getByRole("alertdialog")).toHaveCount(0);
  await expect(dialog.getByLabel("会話を切替")).toHaveValue("");
  await expect(dialog.getByLabel("会話を切替").locator("option:checked")).toHaveText("履歴を選択");

  await dialog.getByRole("button", { name: "音声で入力" }).click();
  await expect(dialog.getByRole("button", { name: "音声認識を停止" })).toBeVisible();
  await expect(dialog.getByText("聞いています。1.2秒の無音で送信します")).toBeVisible();
  await dialog.getByRole("button", { name: "音声認識を停止" }).click();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});
