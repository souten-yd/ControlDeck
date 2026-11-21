import { expect, test, type Locator, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 知識エッセンス（K1b）: 文献の画面（DocumentSheet）の「知識エッセンス」の節。
 *
 *  目的:
 *  - ローカル専用の AI が抜き出した知識（種類 8 つ・引用つき・AI の推論・未確認）を、種類ごとのカードで
 *    見せられること。抽出の開始から Job の進み具合を経て一覧が読み直されること。
 *  - カードの引用が折りたたみで、開くと原文からの引用が出ること。
 *  - 承認・却下・差し戻し（メモは任意）を押したら API が呼ばれ、カードの状態が変わること。
 *  - 原文の版が変わった行（stale）には「原文が変わりました（前の承認は効きません）」の印と、
 *    検めで捨てた件数（形違い・上限）が出ること。
 *  - ローカルのモデルが無い（503）・AI の利用が禁止の文献（422）は、返ってきた理由がそのまま見えること。
 *  - 見るだけの利用者（workflows.edit が無い）には抽出もレビューのボタンが出ないこと。
 *  - 1280 と 320 で横に崩れず、ライト・ダークで撮影すること。
 *
 *  根拠: 2026-11-21 利用者「PatentBuddy の開いている PR は閉じてよい。開発した内容は ControlDeck に
 *  ルールに従って反映して」（知識エッセンス K1 の作り直し。docs/HANDOFF-knowledge-platform.md の K1 の行。
 *  backend は K1a として main 済み）。検証用インスタンスにはローカルのモデルも文献も無いので、
 *  API は page.route で差し替えて確かめる（本物の LLM は呼ばない）。 */

// ---- 差し替える応答（本物の形: backend/app/knowledge/essence.py の list_essences と router.py） ----

const KINDS = {
  problem: "課題", cause: "原因", principle: "原理", mechanism: "作用機構",
  implementation: "実装", effect: "効果", constraint: "制約", application: "応用",
};

const QUOTE = "温度が急上昇したらファンを速く回し、低下時は緩やかに減速する。";

function essenceItem(kind: string, summary: string, quote: string, reviewStatus: string, review: object | null) {
  return { kind, summary, quote, start: 0, end: quote.length, basis: "model_inference", review_status: reviewStatus, review };
}

const FRESH = {
  id: 11, doc_id: 1, source_revision: "rev-2", dropped: 1, status: "succeeded", error: "", job_id: "job-e2e",
  created: 1732140000, updated: 1732147200, basis: "model_inference", stale: false, shard: "patents",
  items: [
    essenceItem("problem", "冷却が追いつかず温度が上がりすぎる", QUOTE, "unreviewed", null),
    essenceItem("effect", "高速の冷却で温度の上昇を抑える", "低下時は緩やかに減速する", "approved",
      { decision: "approved", note: "原文で確認した", reviewer: "e2e-admin", at: 1732147200, stale: false }),
  ],
};
const STALE = {
  id: 10, doc_id: 1, source_revision: "rev-1", dropped: 0, status: "succeeded", error: "", job_id: "job-old",
  created: 1732050000, updated: 1732050000, basis: "model_inference", stale: true, shard: "patents",
  items: [
    essenceItem("principle", "状態の変化の方向で応答の速さを変える", "温度が急上昇したらファンを速く回す", "unreviewed",
      { decision: "approved", note: "", reviewer: "e2e-admin", at: 1732050000, stale: true }),
  ],
};

const DOCUMENT = {
  ref: "patents:1", title: "冷却制御の特許", collection: "公報", collection_kind: "patent", source: "", url: "",
  sensitivity: "confidential", basis: "source", ai_use: "allowed", text: QUOTE, text_truncated: false, chunk_count: 1,
  meta: {}, identifiers: [], references: [], assets: [], annotations: [], analysis_kinds: {}, related: [],
};

/** Knowledge の探す → 文書のシートを開くところまで（応答は差し替え済みの前提）。 */
async function openDocumentSheet(page: Page) {
  await page.goto("/knowledge");
  await page.getByPlaceholder(/言葉・公報番号・規格番号で探す/).fill("冷却");
  await page.getByRole("button", { name: "探す", exact: true }).last().click();
  await page.getByRole("button", { name: /冷却制御の特許/ }).click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByRole("heading", { name: "冷却制御の特許" })).toBeVisible();
  await expect(sheet.getByRole("heading", { name: /知識エッセンス/ })).toBeVisible();
  return sheet;
}

/** 文書とエッセンスと Job の API を差し替える。POST（抽出の開始）は Job を 1 つ返し、
 *  Job は 2 回目の問い合わせまで「実行中」、その後「完了」。withList: 一覧を最初から出すか（見るだけの利用者向け）。 */
async function stubDocumentApis(page: Page, opts: { withList?: boolean } = {}) {
  const state = { extracted: Boolean(opts.withList), jobCalls: 0 };
  await page.route("**/api/v1/knowledge/documents/**", async (route) => {
    const url = route.request().url();
    if (!url.includes("/essences")) {
      await route.fulfill({ json: DOCUMENT });
      return;
    }
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { essences: state.extracted ? [FRESH, STALE] : [], kinds: KINDS } });
      return;
    }
    state.extracted = true;
    await route.fulfill({ status: 201, json: { job_id: "job-e2e", essence_id: 11, essence_ref: "patents:11" } });
  });
  await page.route("**/api/v1/jobs/**", async (route) => {
    state.jobCalls += 1;
    const running = state.jobCalls <= 2;
    await route.fulfill({ json: running
      ? { status: "running", error: "", progress: { status: "ローカルのモデルが知識を抽出中", completed: 1, total: 3 } }
      : { status: "succeeded", error: "", progress: { status: "完了", completed: 3, total: 3 },
          result: { essence_id: 11, essence_ref: "patents:11", items: 2, dropped: 1, unchanged: false } } });
  });
  return state;
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

async function shootEssences(page: Page, sheet: Locator, path: string) {
  await sheet.getByRole("heading", { name: /知識エッセンス/ }).scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await page.screenshot({ path });
}

for (const width of [1280, 320]) {
  test(`Knowledge: 知識エッセンスを抽出して承認・却下・差し戻しで確かめる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/knowledge/find", (route) => route.fulfill({ json: { results: [{
      ref: "patents:1", title: "冷却制御の特許", collection: "公報", relation: "exact", via: "", sensitivity: "confidential",
      passages: [{ text: QUOTE, locator: {} }],
    }] } }));
    await stubDocumentApis(page);
    // レビューは追記だけ。押した判断をそのまま一覧へ反映する（最新のレビューがその件の状態）。
    await page.route("**/api/v1/knowledge/essences/**", async (route) => {
      const body = route.request().postDataJSON() as { decision: string; note: string };
      const index = Number(/\/items\/(\d+)\/review$/.exec(new URL(route.request().url()).pathname)![1]);
      const item = FRESH.items[index];
      item.review_status = body.decision;
      item.review = { decision: body.decision, note: body.note, reviewer: "e2e-admin", at: 1732150000, stale: false };
      await route.fulfill({ json: { essence_id: 11, item_index: index, decision: body.decision, reviewer: "e2e-admin" } });
    });

    const sheet = await openDocumentSheet(page);
    // まだ無ければ「知識を抽出（ローカルの AI）」のボタン → 開始 → Job の進み具合 → 一覧を読み直す
    await sheet.getByRole("button", { name: "知識を抽出（ローカルの AI）" }).click();
    await expect(sheet.getByText("ローカルのモデルが知識を抽出中")).toBeVisible();
    const problem = sheet.locator("li", { hasText: "冷却が追いつかず温度が上がりすぎる" });
    await expect(problem).toBeVisible({ timeout: 10_000 });
    const effect = sheet.locator("li", { hasText: "高速の冷却で温度の上昇を抑える" });
    await expect(effect).toBeVisible();
    await expect(sheet.getByText("検めを通らなかった提案 1 件")).toBeVisible();

    // 種類ごとのまとめ（課題・効果・原理）と、引用の開閉
    for (const name of ["課題", "効果", "原理"]) await expect(sheet.getByText(new RegExp(`^${name} \\(`))).toBeVisible();
    await expect(problem.getByText(QUOTE)).toBeHidden();
    await problem.getByText("引用", { exact: true }).click();
    await expect(problem.getByText(QUOTE)).toBeVisible();

    // はじめから承認済みの件: 状態と前回のレビュー（誰がいつどうした）が見える
    await expect(effect.locator("p", { hasText: "AI の推論（未確認）" })).toHaveText(/承認済み/);
    await effect.getByText("前回のレビュー").click();
    await expect(effect.getByText("原文で確認した")).toBeVisible();

    // メモを添えて承認 → 状態が変わる（API を通して一覧を読み直す）
    await problem.getByText("メモを添える（任意）").click();
    await problem.getByLabel("知識 1 のレビューメモ").fill("図1の制御ブロックで確認した");
    await problem.getByRole("button", { name: "承認", exact: true }).click();
    await expect(problem.locator("p", { hasText: "AI の推論（未確認）" })).toHaveText(/承認済み/, { timeout: 10_000 });
    await problem.getByText("前回のレビュー").click();
    await expect(problem.getByText("図1の制御ブロックで確認した")).toBeVisible();

    // 却下・差し戻しでも同様に最新のレビューがその件の状態になる
    await problem.getByRole("button", { name: "却下", exact: true }).click();
    await expect(problem.locator("p", { hasText: "AI の推論（未確認）" })).toHaveText(/却下/, { timeout: 10_000 });
    await problem.getByRole("button", { name: "差し戻し", exact: true }).click();
    await expect(problem.locator("p", { hasText: "AI の推論（未確認）" })).toHaveText(/差し戻し/, { timeout: 10_000 });

    // 原文の版が変わった行: stale の印と、レビューできない案内
    await expect(sheet.getByText("原文が変わりました（前の承認は効きません）")).toBeVisible();
    const staleCard = sheet.locator("li", { hasText: "状態の変化の方向で応答の速さを変える" });
    await expect(staleCard.getByText("原文が変わっているためレビューできません", { exact: false })).toBeVisible();
    await expect(staleCard.getByRole("button", { name: "承認", exact: true })).toHaveCount(0);

    await shootEssences(page, sheet, `test-results/knowledge-essence-${width}.png`);
    await page.emulateMedia({ colorScheme: "dark" });
    await shootEssences(page, sheet, `test-results/knowledge-essence-dark-${width}.png`);
    await noHorizontalOverflow(page);
  });

  test(`Knowledge: 知識の抽出が始められないときは理由を出す（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/knowledge/find", (route) => route.fulfill({ json: { results: [{
      ref: "patents:1", title: "冷却制御の特許", collection: "公報", relation: "exact", via: "", sensitivity: "confidential",
      passages: [{ text: QUOTE, locator: {} }],
    }] } }));
    // 1 回目は 503（ローカルのモデルが無い）、2 回目は 422（利用条件で AI に使えない文献）。
    // 一覧は空のまま（開始に失敗するので一覧は増えない）。
    const failures = [
      { status: 503, detail: "ローカル専用のモデル（ControlDeck が管理する llama.cpp の GGUF）が使えません。"
        + "案件の内容は外部へ送らないため、Models でローカルのモデルを選んでから実行してください" },
      { status: 422, detail: "この文書は利用条件により AI に入力できません" },
    ];
    await page.route("**/api/v1/knowledge/documents/**", async (route) => {
      if (route.request().method() === "POST" && route.request().url().includes("/essences")) {
        const fail = failures.shift()!;
        await route.fulfill({ status: fail.status, json: { detail: fail.detail } });
        return;
      }
      if (route.request().url().includes("/essences")) {
        await route.fulfill({ json: { essences: [], kinds: KINDS } });
        return;
      }
      await route.fulfill({ json: DOCUMENT });
    });

    const sheet = await openDocumentSheet(page);
    const start = sheet.getByRole("button", { name: "知識を抽出（ローカルの AI）" });
    await start.click();
    await expect(sheet.getByText("ローカル専用のモデル（ControlDeck が管理する llama.cpp の GGUF）が使えません", { exact: false }))
      .toBeVisible();
    await start.click();
    await expect(sheet.getByText("この文書は利用条件により AI に入力できません")).toBeVisible();
    if (width === 1280) {
      await shootEssences(page, sheet, "test-results/knowledge-essence-error-1280.png");
    }
    await noHorizontalOverflow(page);
  });
}

test("Knowledge: 見るだけの利用者に抽出もレビューのボタンが無い（1280px）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  // 実際のログインの後、/auth/me を workflows.edit の無い利用者に差し替える（画面はこの応答で出し分ける）
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    id: 2, username: "e2e-viewer", display_name: "見るだけ", role: "viewer", permissions: ["workflows.run"],
    totp_enabled: true, recovery_codes_remaining: 0, totp_required: false,
  } }));
  await page.route("**/api/v1/knowledge/find", (route) => route.fulfill({ json: { results: [{
    ref: "patents:1", title: "冷却制御の特許", collection: "公報", relation: "exact", via: "", sensitivity: "confidential",
    passages: [{ text: QUOTE, locator: {} }],
  }] } }));
  await stubDocumentApis(page, { withList: true });

  const sheet = await openDocumentSheet(page);
  // 一覧・カードは見られる（承認済みの状態も見える）が、抽出もレビューの操作は出ない
  await expect(sheet.locator("li", { hasText: "冷却が追いつかず温度が上がりすぎる" })).toBeVisible();
  await expect(sheet.getByText("もう一度抽出する")).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "知識を抽出（ローカルの AI）" })).toHaveCount(0);
  const effect = sheet.locator("li", { hasText: "高速の冷却で温度の上昇を抑える" });
  await expect(effect.locator("p", { hasText: "AI の推論（未確認）" })).toHaveText(/承認済み/);
  for (const name of ["承認", "却下", "差し戻し"]) {
    await expect(sheet.getByRole("button", { name, exact: true })).toHaveCount(0);
  }
  await expect(sheet.getByText("まだ抽出していません。")).toHaveCount(0);
  await shootEssences(page, sheet, "test-results/knowledge-essence-viewer-1280.png");
  await noHorizontalOverflow(page);
});
