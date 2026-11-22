import { expect, test, type Locator, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 知識マップ（K3）: 文献の画面（DocumentSheet）の「知識マップ」と「知識のつながり」。
 *
 *  目的:
 *  - 知識マップ: 1 つの文献を中心に 1〜2 hop の文献と承認済みの知識を SVG の放射図で見ること。
 *    hop の切り替えで広がりが変わり、上限に達したら打ち切りの印が出ること。
 *    ノード（四角=文献・円=知識）を押すとその文献を開くこと。
 *  - 技術仮説のリンク: 承認済みのエッセンスのカードから「別の文献の知識と結ぶ」で
 *    相手を検索して選び、理由を書いて結ぶこと。作っただけでは未確認で、
 *    「知識のつながり」の一覧に承認・却下の別操作が出ること。
 *  - 1280 と 320 で横に崩れず、ライト・ダークで撮影すること。
 *
 *  根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は
 *  ControlDeck にルールに従って反映して」（K3 の作り直し。docs/HANDOFF-knowledge-platform.md
 *  の K3 の行。backend は map.py・links.py）。検証用インスタンスには文献も承認済みの
 *  知識も無いので、API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */

// ---- 差し替える応答（本物の形: backend/app/knowledge/map.py・links.py・essence.py） ----

const KINDS = {
  problem: "課題", cause: "原因", principle: "原理", mechanism: "作用機構",
  implementation: "実装", effect: "効果", constraint: "制約", application: "応用",
};
const COOLING = "冷却装置の制御では、温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。";
const VIBRATION = "振動装置の制御では、回転体の振動を検知したら質量を再配分し、共振を段階的に抑える。";

const QUOTE = "温度が急上昇したらファンを速く回し";

function documentFor(ref: string, title: string, text: string) {
  return {
    ref, title, collection: "公報", collection_kind: "patent", source: "", url: "",
    sensitivity: "confidential", basis: "source", ai_use: "allowed", text, text_truncated: false, chunk_count: 1,
    meta: {}, identifiers: [], references: [], assets: [], annotations: [], analysis_kinds: {}, related: [],
  };
}
const DOCUMENTS: Record<string, ReturnType<typeof documentFor>> = {
  "patents:1": documentFor("patents:1", "冷却制御の特許", COOLING),
  "patents:2": documentFor("patents:2", "振動抑制の特許", VIBRATION),
  "patents:3": documentFor("patents:3", "潤滑改良の特許", "潤滑装置の制御では、摺動部の油膜が切れたら給油を増やし、摩擦を低減する。"),
};

/** K1 の一覧: 1 件目は未確認、2 件目は承認済み（「別の文献の知識と結ぶ」は承認済みだけに出る）。 */
const ESSENCES = {
  essences: [{
    id: 11, doc_id: 1, source_revision: "rev-1", dropped: 0, status: "succeeded", error: "", job_id: "",
    created: 1732140000, updated: 1732140000, basis: "model_inference", stale: false, shard: "patents",
    items: [
      { kind: "problem", summary: "冷却が追いつかず温度が上がりすぎる", quote: QUOTE, start: 0, end: QUOTE.length,
        basis: "model_inference", review_status: "unreviewed", review: null },
      { kind: "effect", summary: "高速の冷却で温度の上昇を抑える", quote: QUOTE, start: 0, end: QUOTE.length,
        basis: "model_inference", review_status: "approved",
        review: { decision: "approved", note: "原文で確認した", reviewer: "e2e-admin", at: 1732150000, stale: false } },
    ],
  }],
  kinds: KINDS,
};

const COOL_NODE = { id: "d:patents:1", kind: "document", ref: "patents:1", title: "冷却制御の特許", relation: "root", depth: 0 };
const COOL_EFFECT = { id: "k:patents:11:1", kind: "essence", essence_ref: "patents:11", item_index: 1, doc_ref: "patents:1",
  essence_kind: "effect", summary: "高速の冷却で温度の上昇を抑える", reviewed_at: 1732150000 };
const VIB_NODE = { id: "d:patents:2", kind: "document", ref: "patents:2", title: "振動抑制の特許", relation: "cites",
  depth: 1, via: "特許第1000003号" };
const VIB_PROBLEM = { id: "k:patents:12:0", kind: "essence", essence_ref: "patents:12", item_index: 0, doc_ref: "patents:2",
  essence_kind: "problem", summary: "回転体の振動が問題になる", reviewed_at: 1732150000 };
const FAR_NODE = { id: "d:patents:3", kind: "document", ref: "patents:3", title: "潤滑改良の特許", relation: "cites",
  depth: 2, via: "特許第1000004号" };
const FAR_ESSENCE = { id: "k:patents:13:0", kind: "essence", essence_ref: "patents:13", item_index: 0, doc_ref: "patents:3",
  essence_kind: "principle", summary: "油膜の再形成で摩擦を下げる", reviewed_at: 1732150000 };

const MAP_BASE = {
  nodes: [COOL_NODE, COOL_EFFECT, VIB_NODE, VIB_PROBLEM],
  edges: [
    { source: "d:patents:1", target: "d:patents:2", relation: "cites" },
    { source: "d:patents:1", target: "k:patents:11:1", relation: "evidence" },
    { source: "d:patents:2", target: "k:patents:12:0", relation: "evidence" },
  ],
  limits: { nodes: 61, edges: 60 },
  kinds: KINDS,
  relations: { cites: "参照", cited_by: "参照される", version: "別の版", evidence: "出典" },
};
const MAP_1HOP = { ...MAP_BASE, hops: 1, truncated: false };
const MAP_2HOP = {
  ...MAP_BASE, hops: 2, truncated: true,
  nodes: [...MAP_BASE.nodes, FAR_NODE, FAR_ESSENCE,
    { id: "d:patents:4", kind: "document", ref: "patents:4", title: "加湿制御の特許", relation: "cites", depth: 2, via: "特許第1000005号" }],
  edges: [...MAP_BASE.edges,
    { source: "d:patents:2", target: "d:patents:3", relation: "cites" },
    { source: "d:patents:3", target: "k:patents:13:0", relation: "evidence" },
    { source: "d:patents:3", target: "d:patents:4", relation: "cites" }],
};

/** LinkMaker の相手検索（K2 の横断検索と同じ形。承認済みだけが返る）。 */
const SEARCH_RESULT = {
  items: [{ essence_ref: "patents:12", item_index: 0, doc_ref: "patents:2", doc_title: "振動抑制の特許",
    kind: "problem", summary: "回転体の振動が問題になる", quote: "回転体の振動を検知したら", reviewed_at: 1732150000 }],
  next_cursor: null, limit: 50, kinds: KINDS,
};

interface StubLink {
  link_ref: string; basis: string; note: string; created: number; stale: boolean;
  review_status: string;
  review: { decision: string; note: string; reviewer: string; at: number; stale: boolean } | null;
  source: Record<string, unknown>; target: Record<string, unknown>;
}

const SOURCE_SIDE = { doc_ref: "patents:1", doc_title: "冷却制御の特許", essence_ref: "patents:11", item_index: 1,
  kind: "effect", summary: "高速の冷却で温度の上昇を抑える", reviewed_at: 1732150000 };
const TARGET_SIDE = { doc_ref: "patents:2", doc_title: "振動抑制の特許", essence_ref: "patents:12", item_index: 0,
  kind: "problem", summary: "回転体の振動が問題になる", reviewed_at: 1732150000 };

/** API を差し替える。作ったリンクとレビューは state に入れて、一覧の読み直しに反映する。 */
async function stubKnowledgeApis(page: Page) {
  const state = { links: [] as StubLink[] };
  await page.route("**/api/v1/knowledge/find", (route) => route.fulfill({ json: { results: [{
    ref: "patents:1", title: "冷却制御の特許", collection: "公報", relation: "exact", via: "", sensitivity: "confidential",
    passages: [{ text: COOLING, locator: {} }],
  }] } }));
  await page.route("**/api/v1/knowledge/documents/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/map")) {
      await route.fulfill({ json: url.searchParams.get("hops") === "2" ? MAP_2HOP : MAP_1HOP });
      return;
    }
    if (url.pathname.endsWith("/links")) {
      await route.fulfill({ json: { links: state.links } });
      return;
    }
    if (url.pathname.endsWith("/essences")) {
      await route.fulfill({ json: ESSENCES });
      return;
    }
    const ref = decodeURIComponent(url.pathname.split("/documents/")[1] ?? "patents:1");
    await route.fulfill({ json: DOCUMENTS[ref] ?? DOCUMENTS["patents:1"] });
  });
  await page.route("**/api/v1/knowledge/essences/search", (route) => route.fulfill({ json: SEARCH_RESULT }));
  await page.route("**/api/v1/knowledge/essences/*/items/*/links", async (route) => {
    const body = route.request().postDataJSON() as { note: string };
    state.links.push({
      link_ref: "patents:1", basis: "inferred", note: body.note, created: 1732150000,
      stale: false, review_status: "unreviewed", review: null,
      source: SOURCE_SIDE, target: TARGET_SIDE,
    });
    await route.fulfill({ status: 201, json: { link_ref: "patents:1", basis: "inferred", review_status: "unreviewed",
      source: SOURCE_SIDE, target: TARGET_SIDE, note: body.note } });
  });
  await page.route("**/api/v1/knowledge/links/*/review", async (route) => {
    const body = route.request().postDataJSON() as { decision: string; note?: string };
    const link = state.links[0];
    link.review_status = body.decision;
    link.review = { decision: body.decision, note: body.note ?? "", reviewer: "e2e-admin", at: 1732150000, stale: false };
    await route.fulfill({ json: { link_ref: link.link_ref, decision: body.decision, reviewer: "e2e-admin" } });
  });
  return state;
}

/** Knowledge の探す → 文書のシートを開くところまで（応答は差し替え済みの前提）。 */
async function openDocumentSheet(page: Page) {
  await page.goto("/knowledge");
  await page.getByPlaceholder(/言葉・公報番号・規格番号で探す/).fill("冷却");
  await page.getByRole("button", { name: "探す", exact: true }).last().click();
  await page.getByRole("button", { name: "冷却制御の特許" }).first().click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByRole("heading", { name: "冷却制御の特許" })).toBeVisible();
  return sheet;
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function shoot(page: Page, path: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path, fullPage: true });
}

for (const width of [1280, 320]) {
  test(`Knowledge: 知識マップで関係を見て、技術仮説のリンクを結んで承認する（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await stubKnowledgeApis(page);

    // ---- 知識マップ: 放射図・hop の切り替え・打ち切りの印・ノードで文献を開く ----
    const sheet = await openDocumentSheet(page);
    // まだリンクが無いときの案内（エッセンスのカードから作れることが書いてある）
    await expect(sheet.getByText("まだありません。知識エッセンスのカードから「別の文献の知識と結ぶ」で作れます。")).toBeVisible();
    const mapHeading = sheet.getByRole("heading", { name: /知識マップ/ });
    await mapHeading.scrollIntoViewIfNeeded();
    const figure = sheet.getByRole("group", { name: "知識マップの図" });
    await expect(figure).toBeVisible();
    await expect(sheet.getByText("4/61 ノード")).toBeVisible();
    await expect(figure.getByRole("button", { name: "文献 冷却制御の特許" })).toBeVisible();
    await expect(figure.getByRole("button", { name: "文献 振動抑制の特許" })).toBeVisible();
    // 2 hop まで広げる: 図が広がり、上限に達したら打ち切りの印
    await sheet.getByRole("button", { name: "2 hop まで" }).click();
    await expect(sheet.getByText("7/61 ノード")).toBeVisible();
    await expect(sheet.getByText("上限に達したため一部だけ表示")).toBeVisible();
    await expect(figure.getByRole("button", { name: "文献 潤滑改良の特許" })).toBeVisible();
    // 1 段目の文献ノードを押すと、その文献のシートへ切り替わる
    await figure.getByRole("button", { name: "文献 振動抑制の特許" }).click();
    await expect(sheet.getByRole("heading", { name: "振動抑制の特許" })).toBeVisible();
    // 2 段目の知識の円も描画の中に収まって押せる（viewBox の端で切れないこと）
    await sheet.getByRole("button", { name: "閉じる" }).click();
    await page.getByPlaceholder(/言葉・公報番号・規格番号で探す/).fill("冷却");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await page.getByRole("button", { name: "冷却制御の特許" }).first().click();
    await expect(sheet.getByRole("heading", { name: "冷却制御の特許" })).toBeVisible();
    await sheet.getByRole("button", { name: "2 hop まで" }).click();
    await expect(figure.getByRole("button", { name: "原理: 油膜の再形成で摩擦を下げる" })).toBeVisible();
    await figure.getByRole("button", { name: "原理: 油膜の再形成で摩擦を下げる" }).click();
    await expect(sheet.getByRole("heading", { name: "潤滑改良の特許" })).toBeVisible();

    // ---- 技術仮説のリンク: 承認済みの知識から別の文献へ結ぶ → 別の操作で承認 ----
    await sheet.getByRole("button", { name: "閉じる" }).click();
    await page.getByRole("button", { name: "冷却制御の特許" }).first().click();
    await expect(sheet.getByRole("heading", { name: "冷却制御の特許" })).toBeVisible();
    // 承認済みの知識のカードだけに「別の文献の知識と結ぶ」が出る
    const approvedCard = sheet.locator("li", { hasText: "高速の冷却で温度の上昇を抑える" });
    await approvedCard.getByRole("button", { name: "別の文献の知識と結ぶ" }).click();
    await sheet.getByPlaceholder(/相手の知識を探す/).fill("振動");
    await sheet.getByRole("button", { name: "探す", exact: true }).click();
    await sheet.getByRole("button", { name: /相手を選ぶ: 振動抑制の特許/ }).click();
    await sheet.getByLabel("結ぶ理由").fill("同じ制御思想を振動に適用できる");
    await sheet.getByRole("button", { name: "結ぶ", exact: true }).click();
    // 「知識のつながり」に未確認で出る
    const linksHeading = sheet.getByRole("heading", { name: /知識のつながり/ });
    await linksHeading.scrollIntoViewIfNeeded();
    const linkCard = sheet.locator("li", { hasText: "この文献から" });
    await expect(linkCard.getByText("未確認", { exact: true })).toBeVisible();
    await expect(linkCard.getByText("理由: 同じ制御思想を振動に適用できる")).toBeVisible();
    await expect(linkCard.getByText(/振動抑制の特許/)).toBeVisible();
    // メモを添えて承認する（メモはレビューと一緒に届くことを前回のレビューの展開で確かめる）
    await linkCard.getByText("メモを添える（任意）").click();
    await sheet.getByLabel("リンクのレビューメモ").fill("原文の両方で確かめた");
    await linkCard.getByRole("button", { name: "承認", exact: true }).click();
    await expect(linkCard.getByText("承認済み", { exact: true })).toBeVisible();
    await linkCard.getByText("前回のレビュー").click();
    await expect(linkCard.getByText(/e2e-admin ·/)).toBeVisible();
    await expect(linkCard.getByText("原文の両方で確かめた")).toBeVisible();

    await shoot(page, `test-results/knowledge-map-${width}.png`);
    await page.emulateMedia({ colorScheme: "dark" });
    await shoot(page, `test-results/knowledge-map-dark-${width}.png`);
    await noHorizontalOverflow(page);
  });
}
