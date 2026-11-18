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
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
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

  const dialog = page.locator("[data-assistant-shell]");
  // 専用の「閉じる」は無い。殻と同じ ☰ から同じドロワーで移動する。
  await expect(dialog.getByRole("button", { name: "AIアシスタントを閉じる" })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "メニューを開く" })).toBeVisible();
  // 会話の切替・新規・削除は履歴（上の行のボタンで開くシート）へ、機能の選択は入力欄の札へ移した。
  await expect(dialog.getByRole("button", { name: "選択中の会話を削除" })).toHaveCount(0);
  await expect(dialog.locator("[data-assistant-topbar] select")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "会話の履歴" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  const plus = dialog.locator("[data-assistant-plus]");
  const detected = dialog.locator("[data-mode-detected]");
  const modePill = dialog.locator('[data-composer-pill="mode"] [data-pill-label]');
  const pickMode = async (id: string) => {
    await plus.click();
    await page.getByRole("menu", { name: "添付・機能・権限" }).locator(`[data-mode-option="${id}"]`).click();
  };
  await expect(detected).toContainText("自動");
  const textarea = dialog.locator("textarea");
  await expect(textarea).toBeVisible();
  await textarea.fill("最新のGPUニュースを検索して");
  await expect(detected).toContainText("Web Search");
  await pickMode("investigate");
  await expect(modePill).toHaveText("調査");
  await dialog.getByLabel("調べる深さ").selectOption("academic");
  await pickMode("auto");
  await textarea.fill("20ノードのワークフローを作って");
  await expect(detected).toContainText("Generate Flow");
  await pickMode("investigate");
  await dialog.getByLabel("調べる深さ").selectOption("deep");
  await dialog.getByRole("button", { name: "AI設定を開く" }).click();
  await expect(dialog.getByText("Deep Research 検索深度")).toBeVisible();
  await expect(dialog.getByText("添付PDF・文書は会話RAGから再利用します。", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await dialog.getByRole("button", { name: "AI設定を閉じる" }).click();
  await pickMode("auto");
  await textarea.fill("mobile-overflow-" + "abcdefghijklmnopqrstuvwxyz0123456789".repeat(30));

  const measure = () => page.evaluate(() => {
    const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().toJSON() as DOMRect;
    const input = document.querySelector<HTMLTextAreaElement>("[data-assistant-input]")!;
    const composer = document.querySelector<HTMLElement>("[data-assistant-composer]")!;
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      shell: rect("[data-assistant-shell]"),
      input: input.getBoundingClientRect().toJSON() as DOMRect,
      inputFontSize: Number.parseFloat(getComputedStyle(input).fontSize),
      mic: rect('[aria-label="音声で入力"]'),
      menu: rect('[data-assistant-shell] [aria-label="メニューを開く"]'),
      history: rect("[data-assistant-history-button]"),
      chip: rect("[data-assistant-plus]"),
      send: rect('[data-assistant-shell] [aria-label="送信"]'),
      topbar: rect("[data-assistant-topbar]"),
      messagesOverscroll: getComputedStyle(document.querySelector("[data-assistant-messages]")!).overscrollBehaviorY,
      rootOverscroll: getComputedStyle(document.documentElement).overscrollBehaviorY,
      rootOverflow: getComputedStyle(document.documentElement).overflowY,
      composer: composer.getBoundingClientRect().toJSON() as DOMRect,
      composerBackground: getComputedStyle(composer).backgroundColor,
      inputCard: rect("[data-assistant-input-card]"),
      inputRow: rect("[data-assistant-input-row]"),
      statusRow: rect("[data-assistant-composer-status]"),
      statusRows: document.querySelectorAll("[data-assistant-composer-status]").length,
      gap: rect(".assistant-composer-gap"),
    };
  });
  const layout = await measure();
  console.log("ASSISTANT_MOBILE_LAYOUT", JSON.stringify(layout));
  expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.shell.right).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.inputCard.right).toBeLessThanOrEqual(layout.viewportWidth - 12);
  expect(layout.input.right).toBeLessThanOrEqual(layout.inputCard.right);
  expect(layout.send.right).toBeLessThanOrEqual(layout.inputCard.right);
  for (const target of [layout.mic, layout.menu, layout.history, layout.send]) {
    expect(target.width).toBeGreaterThanOrEqual(44);
    expect(target.height).toBeGreaterThanOrEqual(44);
  }
  expect(layout.chip.height).toBeGreaterThanOrEqual(44);
  expect(layout.chip.right).toBeLessThanOrEqual(layout.mic.left);
  expect(layout.menu.left).toBeLessThan(16);
  // 動くのは会話の一覧だけ。文書は動かさず、上の行と入力欄は画面の端に留まる。
  expect(layout.topbar.top).toBe(0);
  expect(layout.topbar.height).toBe(57); // 殻の上の行と同じ 1 段（56px + 罫線）
  expect(layout.messagesOverscroll).toBe("contain");
  expect(layout.rootOverscroll).toBe("none");
  expect(layout.rootOverflow).toBe("hidden");
  expect(layout.inputFontSize).toBeGreaterThanOrEqual(16);
  expect(layout.statusRows).toBe(1);
  expect(layout.composer.bottom).toBe(layout.shell.bottom);
  expect(layout.shell.bottom).toBe(layout.viewportHeight);
  expect(layout.composerBackground).toBe("rgba(0, 0, 0, 0)");
  // 入力カードの中は本文が上、道具の行が下。状態の行はカードの下。
  expect(layout.inputRow.top).toBeGreaterThanOrEqual(layout.input.bottom - 1);
  // 指で使う端末では、出すものが無いとき状態の行（キーボードの案内）を畳み、ホームバーの帯の分だけ空ける
  // （2026-11-17: iPhone で入力欄の下が空きすぎていた）
  expect(layout.statusRow.height).toBe(0);
  expect(layout.gap.top).toBeGreaterThanOrEqual(layout.inputCard.bottom);
  expect(layout.gap.bottom).toBeLessThanOrEqual(layout.viewportHeight);
  await expect(dialog.getByText("Enterで送信 · Shift+Enterで改行")).toBeHidden();
  await expect(page.getByRole("navigation", { name: "メインナビゲーション" })).toBeHidden();

  const idleInputTop = layout.inputRow.top;
  await page.route("**/api/v1/chat/asr/status", (route) => route.fulfill({ json: { ready: true, installing: false, job_id: null } }));
  await dialog.getByRole("button", { name: "音声で入力" }).click();
  await expect(dialog.locator("[data-assistant-voice]")).toBeVisible();   // 聞いている間は波形（v0.72 から）
  const activeInputTop = await dialog.locator("[data-assistant-input-row]").evaluate((element) => element.getBoundingClientRect().top);
  // 指で使う端末は、普段畳んでいる状態の行が開く分だけ上がる（それ以上は動かない）
  const activeStatusHeight = await dialog.locator("[data-assistant-composer-status]").evaluate((element) => element.getBoundingClientRect().height);
  expect(idleInputTop - activeInputTop).toBeGreaterThanOrEqual(0);
  expect(idleInputTop - activeInputTop).toBeLessThanOrEqual(activeStatusHeight);
  await testInfo.attach("assistant-mobile-status-active", {
    body: await page.screenshot(),
    contentType: "image/png",
  });
  await dialog.getByRole("button", { name: "音声入力を終える" }).click();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  await testInfo.attach("assistant-mobile-320x700", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => (await measure()).composer.bottom).toBe(844);   // 殻の高さは少し遅れて追う
  const iphoneLayout = await measure();
  console.log("ASSISTANT_IPHONE_LAYOUT", JSON.stringify(iphoneLayout));
  expect(iphoneLayout.documentScrollWidth).toBeLessThanOrEqual(390);
  expect(iphoneLayout.composer.bottom).toBe(iphoneLayout.viewportHeight);
  expect(iphoneLayout.statusRow.height).toBe(0);                                // 出すものが無ければ畳んだまま
  await testInfo.attach("assistant-mobile-390x844", {
    body: await page.screenshot(),
    contentType: "image/png",
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  const desktopLayout = await measure();
  console.log("ASSISTANT_DESKTOP_LAYOUT", JSON.stringify(desktopLayout));
  expect(desktopLayout.documentScrollWidth).toBeLessThanOrEqual(desktopLayout.viewportWidth);
  expect(desktopLayout.bodyScrollWidth).toBeLessThanOrEqual(desktopLayout.viewportWidth);
  expect(desktopLayout.shell.right).toBeLessThanOrEqual(desktopLayout.viewportWidth);

  // 履歴の列から会話を消す（確認つき）と、新しいチャットへ切り替わる。
  const sidebar = page.locator("[data-assistant-history-sidebar]");
  await sidebar.locator(`[data-history-row="${conversationId}"]`).getByRole("button", { name: /の操作$/ }).click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  await page.getByRole("button", { name: "削除する" }).click();
  await expect(sidebar.locator(`[data-history-row="${conversationId}"]`)).toHaveCount(0);
  await expect(dialog.locator("[data-assistant-title]")).toHaveText("新しいチャット");

  await dialog.getByRole("button", { name: "音声で入力" }).click();
  await expect(dialog.locator("[data-assistant-voice]")).toBeVisible();   // 聞いている間は波形（v0.72 から）
  await dialog.getByRole("button", { name: "音声入力を終える" }).click();
  await expect(dialog.getByRole("button", { name: "音声で入力" })).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});

test("shows compact conversation references and inserts one into the prompt", async ({ page }) => {
  test.skip(!username || !password || !referenceConversation, "reference fixture is required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.evaluate((id) => localStorage.setItem("cd-chat-conversation", id), referenceConversation!);
  await page.goto("/assistant");

  const dialog = page.locator("[data-assistant-shell]");
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
