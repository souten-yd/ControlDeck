import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const referenceConversation = process.env.CONTROL_DECK_E2E_REFERENCE_CONV;

test("assistant input stays flush in standalone iPhone viewports", async ({ page }, testInfo) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    localStorage.setItem("cd-theme", "dark");
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
  await expect(page.locator("html")).toHaveClass(/pwa-standalone/);

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
  await dialog.getByLabel("処理モード").selectOption("deep");
  await dialog.getByRole("button", { name: "AI設定を開く" }).click();
  await expect(dialog.getByText("Deep Research 検索深度")).toBeVisible();
  await expect(dialog.getByText("添付PDF・文書は会話RAGから再利用します。", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await dialog.getByRole("button", { name: "AI設定を閉じる" }).click();
  await dialog.getByLabel("処理モード").selectOption("auto");
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
      shell: rect(document.querySelector("[data-assistant-shell]")!).toJSON(),
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
      composer: rect(document.querySelector("[data-assistant-composer]")!).toJSON(),
      inputRow: rect(document.querySelector("[data-assistant-input-row]")!).toJSON(),
      statusRow: rect(document.querySelector("[data-assistant-composer-status]")!).toJSON(),
      statusRows: document.querySelectorAll("[data-assistant-composer-status]").length,
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
  expect(layout.mode.height).toBe(layout.history.height);
  expect(layout.mode.width).toBe(112);
  expect(layout.history.width).toBeGreaterThanOrEqual(60);
  expect(layout.history.width).toBeGreaterThanOrEqual(80);
  expect(layout.mode.height).toBe(36);
  expect(layout.inputFontSize).toBeGreaterThanOrEqual(16);
  expect(layout.inputMinWidth).toBe("0px");
  expect(layout.statusRows).toBe(1);
  expect(layout.shell.bottom).toBe(layout.dialog.bottom);
  expect(layout.composer.bottom).toBe(layout.dialog.bottom);
  expect(layout.inputRow.bottom).toBe(layout.dialog.bottom);
  expect(layout.statusRow.bottom).toBeLessThanOrEqual(layout.inputRow.top);
  await expect(page.getByRole("navigation", { name: "メインナビゲーション" })).toBeHidden();

  const idleInputTop = layout.inputRow.top;
  await dialog.getByRole("button", { name: "音声で入力" }).click();
  await expect(dialog.getByText("聞いています。1.2秒の無音で送信します")).toBeVisible();
  const activeInputTop = await dialog.locator("[data-assistant-input-row]").evaluate((element) => element.getBoundingClientRect().top);
  expect(activeInputTop).toBe(idleInputTop);
  await testInfo.attach("assistant-mobile-status-active", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  await dialog.getByRole("button", { name: "音声認識を停止" }).click();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  await testInfo.attach("assistant-mobile-320x700", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

  await page.setViewportSize({ width: 390, height: 844 });
  const iphoneLayout = await page.evaluate(() => {
    const shell = document.querySelector<HTMLElement>("[data-assistant-shell]")!.getBoundingClientRect();
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!.getBoundingClientRect();
    const composer = document.querySelector<HTMLElement>("[data-assistant-composer]")!;
    const row = document.querySelector<HTMLElement>("[data-assistant-input-row]")!.getBoundingClientRect();
    return {
      shellBottom: shell.bottom,
      dialogBottom: dialog.bottom,
      composerBottom: composer.getBoundingClientRect().bottom,
      rowBottom: row.bottom,
      composerPaddingBottom: Number.parseFloat(getComputedStyle(composer).paddingBottom),
      documentScrollWidth: document.documentElement.scrollWidth,
    };
  });
  console.log("ASSISTANT_IPHONE_LAYOUT", JSON.stringify(iphoneLayout));
  expect(iphoneLayout.documentScrollWidth).toBeLessThanOrEqual(390);
  expect(iphoneLayout.shellBottom).toBe(iphoneLayout.dialogBottom);
  expect(iphoneLayout.composerBottom).toBe(iphoneLayout.dialogBottom);
  expect(iphoneLayout.rowBottom).toBe(iphoneLayout.dialogBottom);
  expect(iphoneLayout.composerPaddingBottom).toBe(0);
  await testInfo.attach("assistant-mobile-390x844", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

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

test("shows compact conversation references and inserts one into the prompt", async ({ page }) => {
  test.skip(!username || !password || !referenceConversation, "reference fixture is required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.evaluate((id) => localStorage.setItem("cd-chat-conversation", id), referenceConversation!);
  await page.goto("/assistant");

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("会話内文献（1 件）")).toBeVisible();
  await dialog.locator("details").first().locator("summary").click();
  await expect(dialog.getByText("探索 4 round")).toBeVisible();
  await expect(dialog.getByText("CTX 256K 適用")).toBeVisible();
  await expect(dialog.getByRole("listitem").getByText("[R1]", { exact: true })).toBeVisible();
  const referenceButton = dialog.getByRole("button", { name: "R1を入力欄で参照" });
  await expect(referenceButton).toBeVisible();
  await expect(referenceButton).toHaveCSS("height", "36px");
  await referenceButton.click();
  await expect(dialog.locator('textarea[placeholder="メッセージを入力..."]')).toHaveValue("[R1] ");

  const widths = await dialog.locator("ol").last().evaluate((list) => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    right: list.getBoundingClientRect().right,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  expect(widths.right).toBeLessThanOrEqual(widths.viewport - 12);

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(dialog.getByRole("listitem").getByText("[R1]", { exact: true })).toBeVisible();
  const desktop = await dialog.locator("ol").last().evaluate((list) => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    right: list.getBoundingClientRect().right,
  }));
  expect(desktop.document).toBeLessThanOrEqual(desktop.viewport);
  expect(desktop.right).toBeLessThanOrEqual(desktop.viewport - 12);
});
