/** Models の「TTS / ASR」タブと、設定の機能追加の「音声（TTS / ASR）」。
 *
 * 目的: ASR と TTS が 1 つのタブに並び、使うエンジンを選べ、320px でも横にはみ出さないこと。
 * Supertonic 3 が導入済みなら、試し聞きで本物の WAV が返ること。
 * 根拠: 2026-11-19 利用者「TTS と ASR は同じ項目として追加。各 TTS / ASR の利用するエンジンを選択できるように」。 */
import { expect, test } from "@playwright/test";
import { establishSession } from "./support/session";

const shots = process.env.CONTROL_DECK_E2E_SHOTS ?? "/tmp";

for (const width of [320, 1280]) {
  test(`TTS / ASR tab lists engines and switches them at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/models?tab=speech");
    await expect(page.getByRole("heading", { name: "ASR（音声認識）" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "TTS（音声合成）" })).toBeVisible();
    await expect(page.locator("span", { hasText: /^Supertonic 3$/ })).toBeVisible();
    await expect(page.locator("span", { hasText: /^Haas-ja v1\.0$/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${shots}/speech-tab-${width}.png`, fullPage: true });

    const asr = page.getByLabel("ASR（音声認識）で使うエンジン");
    const before = await asr.inputValue();
    const other = before === "haas" ? "whisper" : "haas";
    const saved = page.waitForResponse((r) => r.url().endsWith("/speech/engines") && r.request().method() === "PUT");
    await asr.selectOption(other);
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await expect(page.getByLabel("ASR（音声認識）で使うエンジン")).toHaveValue(other);
    const restored = page.waitForResponse((r) => r.url().endsWith("/speech/engines") && r.request().method() === "PUT");
    await page.getByLabel("ASR（音声認識）で使うエンジン").selectOption(before);
    await restored;

    // 英語の ASR が並び、選んだ ASR に対応する設定と入力フィルター（初めは推奨）が出る（2026-11-19 利用者の依頼）
    await expect(page.locator("span", { hasText: /^Parakeet TDT 0\.6B v2$/ })).toBeVisible();
    // 日英を区切りごとに聞き分ける ASR（2026-11-19 利用者「日本語と英語が交互に来るときは」）
    await expect(page.locator("span", { hasText: /^日英自動（Haas-ja \/ Parakeet）$/ })).toBeVisible();
    const filter = page.locator("[data-input-filter]");
    if (width === 320) await expect(filter.getByRole("radio", { name: "推奨" })).toHaveAttribute("aria-checked", "true");   // 初めは推奨
    const savedPreset = page.waitForResponse((r) => r.url().endsWith("/speech/engines") && r.request().method() === "PUT");
    await filter.getByRole("radio", { name: "騒がしい場所" }).click();
    expect((await (await savedPreset).json()).selected.input_filter.vad_threshold).toBe(0.6);
    await filter.getByText("詳細な設定").click();
    const minSpeech = filter.getByLabel("声とみなす最短の長さ");
    await expect(minSpeech).toHaveValue("400");
    const savedCustom = page.waitForResponse((r) => r.url().endsWith("/speech/engines") && r.request().method() === "PUT");
    await minSpeech.fill("600");
    await minSpeech.press("Enter");
    expect((await (await savedCustom).json()).selected.input_filter.preset).toBe("custom");
    await expect(filter.getByText("カスタム", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${shots}/speech-filter-${width}.png`, fullPage: true });
    await filter.getByRole("radio", { name: "推奨" }).click();
    await expect(filter.getByRole("radio", { name: "推奨" })).toHaveAttribute("aria-checked", "true");

    const engines = await (await page.request.get("/api/v1/speech/engines")).json();
    if (engines.engines.some((e: { id: string; ready: boolean }) => e.id === "supertonic-3" && e.ready)) {
      const spoken = page.waitForResponse((r) => r.url().endsWith("/speech/tts"));
      await page.getByRole("button", { name: "再生", exact: true }).click();
      const response = await spoken;
      expect(response.status()).toBe(200);
      expect(response.headers()["content-type"]).toBe("audio/wav");
      await expect(page.locator("audio")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `${shots}/speech-tab-played-${width}.png`, fullPage: true });
    }
    expect(errors).toEqual([]);
  });
}

test("Supertonic 3 is offered under 音声 in the feature list", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await establishSession(page, context);
  await page.goto("/settings/features");
  const row = page.locator("#feature-supertonic-3");
  await row.scrollIntoViewIfNeeded();
  await expect(row).toBeVisible();
  await expect(page.getByRole("heading", { name: /音声（TTS \/ ASR）/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await row.screenshot({ path: `${shots}/speech-feature-320.png` });
});

test("the CPU reranker is offered under 検索（RAG） in the feature list", async ({ page, context }) => {
  // 2026-11-20 利用者「reranker は CPU に入れる形で」。導入前は「導入」だけで、更新ボタンは出さない（版固定）
  await establishSession(page, context);
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/settings/features");
    const row = page.locator("#feature-ruri-reranker");
    await row.scrollIntoViewIfNeeded();
    await expect(row).toBeVisible();
    await expect(row).toContainText("ruri-v3-reranker-310m");
    await expect(page.getByRole("heading", { name: /検索（RAG）/ })).toBeVisible();
    await expect(row.getByRole("button", { name: "更新" })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await row.screenshot({ path: `${shots}/retrieval-feature-${width}.png` });
  }
});
