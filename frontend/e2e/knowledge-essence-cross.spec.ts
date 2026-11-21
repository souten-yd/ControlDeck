import { expect, test, type Locator, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 知識の横断（K2）: Knowledge の「知識の横断」タブ。
 *
 *  目的:
 *  - 検索の欄と種類の絞り込みで、承認済みの知識エッセンスを横断して探せること。
 *    結果のカードには文献の題名（押すと文献の画面が開く）・種類・要約・引用（折りたたみ）・
 *    承認の日時が出ること。
 *  - 「比較に加える」で別の文献 2〜3 件を選び、「比較する」で種類ごとの表が見えること。
 *    比較は並べて見せるだけ（AI の判断は足さないことが画面にも書いてある）。
 *  - ページ送り（next_cursor があるとき「もっと見る」）で続きが取れること。
 *  - 何も見つからないとき、文献の画面で知識を抽出・承認するよう案内すること。
 *  - 1280 と 320 で横に崩れず（比較の表は横スクロール）、ライト・ダークで撮影すること。
 *
 *  根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容を
 *  ControlDeck にルールに従って反映して」（K2 の作り直し。docs/HANDOFF-knowledge-platform.md の
 *  K2 の行。backend は cross.py として main 済み）。検証用インスタンスには文献も承認済みの
 *  知識も無いので、API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */

// ---- 差し替える応答（本物の形: backend/app/knowledge/cross.py の search・compare） ----

const KINDS = {
  problem: "課題", cause: "原因", principle: "原理", mechanism: "作用機構",
  implementation: "実装", effect: "効果", constraint: "制約", application: "応用",
};

const COOLING = "冷却装置の制御では、温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。";
const VIBRATION = "振動装置の制御では、回転体の振動を検知したら質量を再配分し、共振を段階的に抑える。";

/** 検索結果のカード（文献つき）。 */
function crossItem(essenceRef: string, index: number, docRef: string, docTitle: string,
                   kind: string, summary: string, quote: string, reviewedAt = 1732150000) {
  return { essence_ref: essenceRef, item_index: index, doc_ref: docRef, doc_title: docTitle,
           kind, summary, quote, reviewed_at: reviewedAt };
}

const COOL_PRINCIPLE = crossItem("patents:11", 0, "patents:1", "冷却制御の特許", "principle",
  "温度の上がり方でファンの速さを変える", "温度が急上昇したらファンを速く回し");
const COOL_EFFECT = crossItem("patents:11", 1, "patents:1", "冷却制御の特許", "effect",
  "下がるときは穏やかに遅くなる", "低下時は緩やかに減速する");
const VIB_PROBLEM = crossItem("patents:12", 0, "patents:2", "振動抑制の特許", "problem",
  "回転体の振動が問題になる", "回転体の振動を検知したら");

/** 比較の表のセル（実応答と同じく、文献の情報は列の見出しにだけ入る）。 */
const COOL_PRINCIPLE_CELL = { essence_ref: "patents:11", item_index: 0, kind: "principle",
  summary: "温度の上がり方でファンの速さを変える", quote: "温度が急上昇したらファンを速く回し", reviewed_at: 1732150000 };
const VIB_PROBLEM_CELL = { essence_ref: "patents:12", item_index: 0, kind: "problem",
  summary: "回転体の振動が問題になる", quote: "回転体の振動を検知したら", reviewed_at: 1732150000 };

const SEARCH_ALL = { items: [COOL_PRINCIPLE, COOL_EFFECT, VIB_PROBLEM], next_cursor: null, limit: 50, kinds: KINDS };
const SEARCH_PRINCIPLE = { items: [COOL_PRINCIPLE], next_cursor: null, limit: 50, kinds: KINDS };
const SEARCH_PAGE1 = { items: [COOL_PRINCIPLE, COOL_EFFECT], next_cursor: 2, limit: 2, kinds: KINDS };
const SEARCH_PAGE2 = { items: [VIB_PROBLEM], next_cursor: null, limit: 2, kinds: KINDS };
const SEARCH_EMPTY = { items: [], next_cursor: null, limit: 50, kinds: KINDS };

const COMPARE = {
  documents: [{ ref: "patents:1", title: "冷却制御の特許" }, { ref: "patents:2", title: "振動抑制の特許" }],
  rows: [
    { kind: "problem", label: "課題", cells: [[], [VIB_PROBLEM_CELL]] },
    { kind: "principle", label: "原理", cells: [[COOL_PRINCIPLE_CELL], []] },
  ],
  kinds: KINDS,
};

const DOCUMENT = {
  ref: "patents:1", title: "冷却制御の特許", collection: "公報", collection_kind: "patent", source: "", url: "",
  sensitivity: "confidential", basis: "source", ai_use: "allowed", text: COOLING, text_truncated: false, chunk_count: 1,
  meta: {}, identifiers: [], references: [], assets: [], annotations: [], analysis_kinds: {}, related: [],
};

/** 検索・比較・文書の API を差し替える。searchKind: 種類の絞り込みをした検索の応答（既定は全件と同じ）。 */
async function stubCrossApis(page: Page, opts: { firstPage?: boolean; searchKind?: object; empty?: boolean } = {}) {
  const state = { moreCalls: 0 };
  await page.route("**/api/v1/knowledge/documents/**", (route) => route.fulfill({ json: DOCUMENT }));
  await page.route("**/api/v1/knowledge/essences/compare", (route) => route.fulfill({ json: COMPARE }));
  await page.route("**/api/v1/knowledge/essences/search", async (route) => {
    const body = route.request().postDataJSON() as { kind: string; cursor: number };
    if (opts.empty) {
      await route.fulfill({ json: SEARCH_EMPTY });
      return;
    }
    if (body.kind === "principle" && opts.searchKind) {
      await route.fulfill({ json: opts.searchKind });
      return;
    }
    if (opts.firstPage && body.cursor === 0) {
      await route.fulfill({ json: SEARCH_PAGE1 });
      return;
    }
    if (opts.firstPage && body.cursor > 0) {
      state.moreCalls += 1;
      await route.fulfill({ json: SEARCH_PAGE2 });
      return;
    }
    await route.fulfill({ json: SEARCH_ALL });
  });
  return state;
}

async function openCrossTab(page: Page) {
  await page.goto("/knowledge");
  await page.getByRole("button", { name: "知識の横断" }).click();
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function shootCross(page: Page, path: string) {
  await page.waitForTimeout(200);
  await page.screenshot({ path, fullPage: true });
}

for (const width of [1280, 320]) {
  test(`Knowledge: 知識の横断で探して種類ごとに比較する（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await stubCrossApis(page, { searchKind: SEARCH_PRINCIPLE });

    await openCrossTab(page);
    // 検索 → 結果のカード（文献の題名・種類・要約・承認の日時・引用の折りたたみ）
    await page.getByPlaceholder(/言葉で知識を探す/).fill("温度 上昇");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await expect(page.getByText("温度の上がり方でファンの速さを変える")).toBeVisible();
    await expect(page.getByText("下がるときは穏やかに遅くなる")).toBeVisible();
    const coolCard = page.locator("li", { hasText: "温度の上がり方でファンの速さを変える" });
    await expect(coolCard.getByText(/承認 /)).toBeVisible();
    await expect(coolCard.getByText(COOLING.slice(0, 20))).toBeHidden();
    await coolCard.getByText("引用", { exact: true }).click();
    await expect(coolCard.getByText("温度が急上昇したらファンを速く回し")).toBeVisible();

    // 種類の絞り込み: 原理だけにすると 1 件になる
    await page.getByLabel("種類の絞り込み").selectOption("principle");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await expect(page.getByText("下がるときは穏やかに遅くなる")).toHaveCount(0);
    await expect(page.getByText("温度の上がり方でファンの速さを変える")).toBeVisible();

    // 絞り込みを戻して、比較に加える（別の文献 2 件）
    await page.getByLabel("種類の絞り込み").selectOption("");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    const vibCard = page.locator("li", { hasText: "回転体の振動が問題になる" });
    await coolCard.getByRole("button", { name: "比較に加える" }).click();
    await expect(coolCard.getByRole("button", { name: /比較に加えてある/ })).toBeVisible();
    await vibCard.getByRole("button", { name: "比較に加える" }).click();
    await expect(page.getByText(/比較に加えた文献 2\/3/)).toBeVisible();

    // 比較する → 種類ごとの表（承認済みを並べるだけであることが書いてある）
    await page.getByRole("button", { name: "比較する（別の文献 2〜3 件）" }).click();
    const table = page.locator("table");
    await expect(table).toBeVisible();
    await expect(page.getByText("比較は承認済みの知識を並べて見せるだけです", { exact: false })).toBeVisible();
    await expect(table.getByText("課題", { exact: true })).toBeVisible();
    await expect(table.getByText("原理", { exact: true })).toBeVisible();
    await expect(table.getByText("回転体の振動が問題になる")).toBeVisible();
    await expect(table.getByText("温度の上がり方でファンの速さを変える")).toBeVisible();

    // 表の引用の開閉と、列の題名から文献の画面へ行く導線
    const problemCell = table.locator("td", { hasText: "回転体の振動が問題になる" });
    await problemCell.getByText("引用", { exact: true }).click();
    await expect(problemCell.getByText("回転体の振動を検知したら")).toBeVisible();
    await table.getByRole("button", { name: "冷却制御の特許" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("heading", { name: "冷却制御の特許" })).toBeVisible();
    await sheet.getByRole("button", { name: "閉じる" }).click();

    await shootCross(page, `test-results/knowledge-essence-cross-${width}.png`);
    await page.emulateMedia({ colorScheme: "dark" });
    await shootCross(page, `test-results/knowledge-essence-cross-dark-${width}.png`);
    await noHorizontalOverflow(page);
  });

  test(`Knowledge: 知識の横断で何も見つからないときは案内する（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await stubCrossApis(page, { empty: true });

    await openCrossTab(page);
    await page.getByPlaceholder(/言葉で知識を探す/).fill("見つからない語");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await expect(page.getByText("文献の画面で知識を抽出し、承認すると、ここで横断して探せます", { exact: false }))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "もっと見る" })).toHaveCount(0);
    await shootCross(page, `test-results/knowledge-essence-cross-empty-${width}.png`);
    await noHorizontalOverflow(page);
  });
}

test("Knowledge: 検索の結果が1画面に収まらないとき「もっと見る」で続きを取る（1280px）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  const state = await stubCrossApis(page, { firstPage: true });

  await openCrossTab(page);
  await page.getByPlaceholder(/言葉で知識を探す/).fill("装置");
  await page.getByRole("button", { name: "探す", exact: true }).last().click();
  // 1 画面目: 2 件。続きがあるので「もっと見る」が出る
  await expect(page.getByText("温度の上がり方でファンの速さを変える")).toBeVisible();
  await expect(page.getByText("回転体の振動が問題になる")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "もっと見る" })).toBeVisible();
  // 続きを取ると 3 件目が足され、「もっと見る」が消える
  await page.getByRole("button", { name: "もっと見る" }).click();
  await expect(page.getByText("回転体の振動が問題になる")).toBeVisible();
  await expect(page.getByRole("button", { name: "もっと見る" })).toHaveCount(0);
  expect(state.moreCalls).toBe(1);
});
