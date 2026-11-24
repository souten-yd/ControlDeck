/** ニュースの画面の「今朝のまとめ」「今週のまとめ」のバブル。API は page.route でモック（外へ通信しない）。
 *  目的: ①どの幅でも 1 行に 2 つの小さなバブルが並ぶ（カルーセル・点は無い） ②タップで 1 つが横幅いっぱいに広がり、中身が開く
 *       ③開いた中身をタップすると小さく戻る。ただし出典のリンク・話題のボタン・出典の開閉では戻らない
 *       ④主な技術・製品の語（出典の記事の話題と一致）と見出し（チャンネル名）をタップすると、新着の一覧がその話題・チャンネルで絞り込まれる。
 *  根拠: 2026-11-24 利用者「今週のまとめと今朝のまとめは、1 行に横並びで 2 つバブルみたいのを出してタップすると、左右横幅フルまで拡大して展開される。
 *       記事の中をタップすると最小化される。気になったトピックスをタップして関連するトピックスに移動できるか」。
 *  撮影: test-results/news-bubbles-{closed,open,topic}-{390,320,1280}[-dark].png（NEWS_SHOTS_DIR があればそこにも写す）。 */
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { establishSession, hasSession } from "./support/session";
import { ITEMS, mockNews } from "./support/news-mock";

const SHOTS = process.env.NEWS_SHOTS_DIR;
async function shot(page: Page, name: string) {
  await page.screenshot({ path: `test-results/${name}.png` });
  if (SHOTS) { mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); }
}
const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

async function setup(page: Page, context: import("@playwright/test").BrowserContext, width: number, height: number, scheme: "light" | "dark") {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ colorScheme: scheme });
  const mocks = await mockNews(page);
  const topicRequests: string[] = [];
  page.on("request", (r) => { const t = new URL(r.url()).searchParams.get("topic"); if (r.url().includes("/news/items") && t) topicRequests.push(t); });
  await context.route(/^https:\/\/example\.com\//, (r) => r.abort());       // 出典のリンク（別タブ）は外へ出さない
  await establishSession(page, context);
  await page.goto("/news");
  return { mocks, topicRequests };
}

for (const [width, height] of [[390, 844], [320, 700], [1280, 900]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`ニュース: まとめのバブル（${width}px・${scheme}）`, async ({ page, context }) => {
      test.skip(!hasSession(), "ログイン情報がありません");
      await setup(page, context, width, height, scheme);
      const sfx = `${width}${scheme === "dark" ? "-dark" : ""}`;
      const daily = page.getByTestId("digest-daily"), weekly = page.getByTestId("digest-weekly");
      const section = page.getByTestId("digests");
      await expect(daily).toBeVisible();
      // ① 1 行に 2 つ。カルーセル・点は無い
      const a = (await daily.boundingBox())!, b = (await weekly.boundingBox())!;
      expect(Math.abs(a.y - b.y)).toBeLessThan(2);
      expect(b.x).toBeGreaterThan(a.x + a.width - 2);
      await expect(page.getByRole("tablist", { name: "まとめの切り替え" })).toHaveCount(0);
      await expect(daily.getByTestId("digest-hint")).toContainText("Qwen の新モデルが出た。");
      expect(await noOverflow(page)).toBe(true);
      await shot(page, `news-bubbles-closed-${sfx}`);

      // ② タップで横幅いっぱいに開く（もう 1 つは小さいまま下に残る）
      await daily.getByRole("button", { name: "今朝のまとめを開く" }).click();
      await expect(daily).toHaveAttribute("data-open", "true");
      await expect(daily.getByTestId("digest-three")).toContainText("今日の 3 行");
      await expect.poll(async () => Math.abs((await daily.boundingBox())!.width - (await section.boundingBox())!.width)).toBeLessThan(2);
      await expect.poll(async () => (await weekly.boundingBox())!.y).toBeGreaterThan((await daily.boundingBox())!.y + (await daily.boundingBox())!.height - 2);
      expect(await noOverflow(page)).toBe(true);
      await shot(page, `news-bubbles-open-${sfx}`);

      // ③ 出典のリンク・出典の開閉・話題では戻らない。中の文字をタップすると戻る
      const popup = page.waitForEvent("popup").catch(() => null);
      await daily.getByTestId("digest-body").getByRole("link", { name: "[1]" }).first().click();
      (await popup)?.close().catch(() => null);
      await expect(daily).toHaveAttribute("data-open", "true");
      await daily.locator(".nw-digest-src > summary").click();
      await expect(daily).toHaveAttribute("data-open", "true");
      await daily.getByTestId("digest-three").locator("li").first().click();
      await expect(daily).toHaveAttribute("data-open", "false");
      await expect.poll(async () => Math.abs((await daily.boundingBox())!.width - (await weekly.boundingBox())!.width)).toBeLessThan(2);

      // 今週を開くと今朝は閉じ、ヘッダーのタップでも戻る
      await daily.getByRole("button", { name: "今朝のまとめを開く" }).click();
      await weekly.getByRole("button", { name: "今週のまとめを開く" }).click();
      await expect(weekly).toHaveAttribute("data-open", "true");
      await expect(daily).toHaveAttribute("data-open", "false");
      await weekly.getByRole("button", { name: "今週のまとめを小さくする" }).click();
      await expect(weekly).toHaveAttribute("data-open", "false");
    });
  }

  test(`ニュース: まとめの話題・見出しをタップすると新着が絞り込まれる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    const { topicRequests } = await setup(page, context, width, height, "light");
    const daily = page.getByTestId("digest-daily");
    await daily.getByRole("button", { name: "今朝のまとめを開く" }).click();
    // 出典の記事の話題と一致する語だけ押せる（Qwen・GGUF・llama.cpp）。一致しない語は文字のまま
    const links = daily.getByTestId("digest-topic-link");
    await expect(links.first()).toBeVisible();
    expect(await links.allTextContents()).toEqual(expect.arrayContaining(["Qwen", "GGUF", "llama.cpp"]));
    await expect(daily.getByTestId("digest-heading-link")).toContainText("ローカル LLM");
    await shot(page, `news-bubbles-topic-link-${width}`);
    await links.filter({ hasText: "Qwen" }).first().click();
    await expect(page.getByRole("heading", { name: "話題「Qwen」" })).toBeVisible();
    expect(topicRequests).toContain("Qwen");
    await expect(page.getByTestId("digests")).toHaveCount(0);       // 絞り込みの画面ではまとめは出ない
    await shot(page, `news-bubbles-topic-${width}`);
    expect(await noOverflow(page)).toBe(true);
    // 見出し（チャンネル名）→ そのチャンネルで絞り込み
    await page.getByRole("tab", { name: "放送" }).click();
    await daily.getByRole("button", { name: "今朝のまとめを開く" }).click();
    await daily.getByTestId("digest-heading-link").click();
    await expect(page.getByRole("button", { name: /CH 01/ }).first()).toHaveAttribute("aria-pressed", "true");
    void ITEMS;
  });
}

test("ニュース: 動きを減らす設定ではバブルの開閉も開く（短い動き）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, context, 390, 844, "light");
  const daily = page.getByTestId("digest-daily");
  await daily.getByRole("button", { name: "今朝のまとめを開く" }).click();
  await expect(daily).toHaveAttribute("data-open", "true");
  await daily.getByTestId("digest-three").locator("li").first().click();
  await expect(daily).toHaveAttribute("data-open", "false");
});
