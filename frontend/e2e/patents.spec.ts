import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Patent（特許調査・検討）画面。案件の作成 → 案件のページ（/patents/:id）→ 概要 → ヒアリング → 期限 → 請求項 → AI 支援 → 分析、
 *  一覧の「分析」（グラフ）と「公報」を、PC 幅と 320px で通す。
 *  根拠: 2026-11-21 利用者「PatentBuddy のアドオンは不要。Patent 機能をつけて」、同日「新しい特許の開発フローがフローティング
 *  ウィンドウになっている。コントロールデックと統合してほしい」（案件はシートではなくページで開く・戻るで一覧へ）と
 *  「特許の分析をグラフで（推移・書誌・請求項・類似・バブル）」。
 *  検証用インスタンスにはローカルのモデルも公報も無いので、AI 支援は「外部へ送らない」案内、分析は空の案内が出ることを確かめ、
 *  グラフの見た目は分析の API の応答を差し替えて撮る。
 *  ウォッチ・公開データの取得（R3-2）: 検索語は外部へ送られるので、確認のチェックを入れるまで保存・取得できないこと、
 *  鍵の無い取得先（EPO）は選べないことを、本物の API の取得先一覧で確かめる。外部へは実際には送らない（「いま取得」は押さない）。 */

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>("[data-shell-main]");
    const doc = document.documentElement;
    return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });
  expect(overflow).toBeLessThanOrEqual(0);
}

/** 本文は殻の main の中で動くので、fullPage では見えている分しか撮れない。いったん画面を本文の高さまで伸ばして撮る。 */
async function shootWhole(page: Page, path: string) {
  const size = page.viewportSize()!;
  const height = await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>("[data-shell-main]");
    return main ? main.scrollHeight + (window.innerHeight - main.clientHeight) : document.documentElement.scrollHeight;
  });
  await page.setViewportSize({ width: size.width, height: Math.min(6000, Math.max(size.height, height)) });
  await page.waitForTimeout(300);
  await page.screenshot({ path, fullPage: true });
  await page.setViewportSize(size);
}

for (const width of [1280, 320]) {
  test(`Patent: 案件のページで期限と請求項を扱う（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/patents");
    await expect(page.getByRole("heading", { name: "Patent" })).toBeVisible();

    const title = `E2E インバータ ${width}`;
    await page.getByRole("button", { name: "案件を作成" }).first().click();
    await page.getByPlaceholder("発明の名称").fill(title);
    await page.getByRole("button", { name: "作成", exact: true }).click();
    // シート（dialog）ではなくページで開く
    await expect(page).toHaveURL(/\/patents\/\d+$/);
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const pane = page.locator('section[aria-labelledby="patent-section-title"]');
    const open = async (name: string) => {
      await page.getByRole("button", { name, exact: true }).click();
      await expect(pane.getByRole("heading", { level: 2, name })).toBeVisible();
    };

    await pane.getByLabel("課題", { exact: true }).fill("スイッチング損失が大きい");
    await pane.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("保存しました")).toBeVisible();
    await shootWhole(page, `test-results/patents-case-summary-${width}.png`);

    await open("ヒアリング");
    await expect(pane.getByRole("button", { name: "話して記録する" })).toBeVisible();
    await pane.getByPlaceholder("話した内容がここに入ります。手で書き足したり直したりもできます。").fill("インバータの損失を減らしたい。ゲートの駆動を工夫した。");
    await pane.getByRole("button", { name: "直した内容を保存" }).click();
    await expect(pane.getByText("文字", { exact: false }).first()).toBeVisible();

    await open("期限");
    await pane.locator('input[type="date"]').fill("2026-01-31");
    await pane.getByRole("button", { name: "出来事を記録" }).click();
    await expect(pane.getByText("審査請求期限（出願日から3年）")).toBeVisible();

    await open("請求項");
    await pane.locator("textarea").first().fill("【請求項１】\nスイッチング素子と、制御部と、を備えるインバータ。");
    await pane.getByRole("button", { name: "新しい版で保存" }).click();
    await expect(pane.getByText("構成要件（最新版）")).toBeVisible();
    await expect(pane.getByText("第 1 版（全 1 版）")).toBeVisible();

    await open("分析");
    await expect(pane.getByText(/に近い公報です|似た公報を探せませんでした/)).toBeVisible();

    await open("AI 支援");
    await expect(pane.getByText("ローカル専用のモデルが使えないため実行できません。案件の内容はクラウドへ送りません。")).toBeVisible();
    await page.waitForTimeout(400);   // タブの色の切り替え（transition）が済んでから撮る
    await shootWhole(page, `test-results/patents-case-${width}.png`);
    await noHorizontalOverflow(page);

    // 区画の切り替えは履歴に積まない。戻ると一覧へ。
    await page.goBack();
    await expect(page).toHaveURL(/\/patents$/);
    await page.getByRole("link", { name: new RegExp(title) }).click();
    await expect(page).toHaveURL(/\/patents\/\d+$/);

    // 削除は ⋯ の中だけ、確認つき
    await page.getByRole("button", { name: "案件のその他の操作" }).click();
    await page.getByRole("menuitem", { name: "案件を削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(page).toHaveURL(/\/patents$/);
    await expect(page.getByRole("link", { name: new RegExp(title) })).toHaveCount(0);
  });

  test(`Patent: 分析のグラフ（応答を差し替え・${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/patents/analytics", (route) => route.fulfill({ json: ANALYTICS }));
    await page.route("**/api/v1/patents/analytics/map", (route) => route.fulfill({ json: MAP }));
    await page.route("**/api/v1/knowledge/documents/**", (route) => route.fulfill({ json: DOCUMENT }));
    await page.goto("/patents?tab=analysis");
    for (const name of ["年別の件数", "出願人ランキング", "IPC サブクラス", "出願人 × IPC", "出願人 × 年", "請求項の数", "国・種別", "類似マップ"]) {
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    }
    await expect(page.getByText("120 件中、書誌が読めたもの", { exact: false })).toBeVisible();
    await expect(page.getByText("近い点ほど内容が似ている", { exact: false })).toBeVisible();
    // 内部の参照（patents:12 など）は出さない
    await expect(page.getByText(/patents:\d+/)).toHaveCount(0);
    await page.waitForTimeout(300);
    await shootWhole(page, `test-results/patents-analysis-${width}.png`);
    await noHorizontalOverflow(page);

    // 点を押すと公報が開き、読み取った書誌は「確認が必要」と出る
    // 点は重なることがあるので、キーボードで開く（押すのと同じ動き）
    await page.getByRole("button", { name: "ゲート駆動回路 1を開く", exact: true }).focus();
    await page.keyboard.press("Enter");
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("読み取り（確認が必要）")).toBeVisible();
    await sheet.getByRole("button", { name: "直す" }).click();
    await expect(sheet.getByLabel("出願人（「、」で区切る）")).toHaveValue("パワー電機株式会社");
    if (width === 320) await page.screenshot({ path: `test-results/patents-biblio-${width}.png` });

    // ダークでも読めること（目で見る用の写真）
    await page.keyboard.press("Escape");
    await page.emulateMedia({ colorScheme: "dark" });
    await shootWhole(page, `test-results/patents-analysis-dark-${width}.png`);
  });
}

for (const width of [1280, 320]) {
  test(`Patent: ウォッチは確認のチェックが要る（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/patents");
    const title = `E2E ウォッチ ${width}`;
    await page.getByRole("button", { name: "案件を作成" }).first().click();
    await page.getByPlaceholder("発明の名称").fill(title);
    await page.getByRole("button", { name: "作成", exact: true }).click();
    await expect(page).toHaveURL(/\/patents\/\d+$/);
    const pane = page.locator('section[aria-labelledby="patent-section-title"]');
    await page.getByRole("button", { name: "ウォッチ", exact: true }).click();
    await expect(pane.getByRole("heading", { level: 2, name: "ウォッチ" })).toBeVisible();

    await pane.getByRole("button", { name: "ウォッチを追加" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.locator("option", { hasText: "EPO" })).toHaveJSProperty("disabled", true);       // 鍵が未設定
    await expect(sheet.locator("option", { hasText: "鍵が未設定" }).first()).toBeAttached();
    await sheet.getByRole("combobox").first().selectOption("openalex");
    await sheet.getByRole("textbox", { name: "検索語" }).fill(`common mode noise inverter ${width}`);
    const add = sheet.getByRole("button", { name: "追加", exact: true });
    await expect(add).toBeDisabled();
    await sheet.getByLabel(/外部の検索サービスへ送られます/).check();
    await expect(add).toBeEnabled();
    // 保存の後の読み直しから、見つかった公報（候補）だけ差し替える。ウォッチの一覧は本物
    await page.route(/\/api\/v1\/patents\/cases\/\d+\/watches$/, async (route) => {
      const real = await (await route.fetch()).json();
      await route.fulfill({ json: { ...real, hits: HITS } });
    });
    await add.click();
    await expect(page.getByText("ウォッチを追加しました")).toBeVisible();
    await expect(pane.getByText(`common mode noise inverter ${width}`)).toBeVisible();
    await expect(pane.locator("li", { hasText: `common mode noise inverter ${width}` }).getByText("OpenAlex（論文） · 1 週間ごと")).toBeVisible();

    // 見つかった公報の並び（候補は差し替え。押すと本物の API に無い ID を送るので、見るだけ）
    await expect(pane.getByText("インバータのコモンモード雑音を抑える回路")).toBeVisible();
    await expect(pane.getByRole("button", { name: /先行文献に登録/ }).first()).toBeVisible();
    await expect(pane.getByRole("button", { name: "処理済み（2）" })).toBeVisible();
    await shootWhole(page, `test-results/patents-watch-${width}.png`);
    await noHorizontalOverflow(page);

    await page.unroute(/\/api\/v1\/patents\/cases\/\d+\/watches$/);
    await page.getByRole("button", { name: "案件のその他の操作" }).click();
    await page.getByRole("menuitem", { name: "案件を削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(page).toHaveURL(/\/patents$/);
  });
}

test("Patent: 公報が無いときの分析と公報の画面（1280px）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  await page.goto("/patents");
  await page.getByRole("button", { name: "分析", exact: true }).click();
  await expect(page.getByText(/まだ公報がありません|年別の件数/).first()).toBeVisible();
  await page.getByRole("button", { name: "公報", exact: true }).click();
  await expect(page.getByPlaceholder(/公報番号で探す/)).toBeVisible();
  await expect(page.getByRole("button", { name: "公報・論文を取り込む" })).toBeVisible();

  // 公開データの取得も、確認のチェックを入れるまで送れない
  await page.getByRole("button", { name: "公開データを取得" }).click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.locator("option", { hasText: "EPO" })).toHaveJSProperty("disabled", true);
  await sheet.getByRole("textbox", { name: "検索語" }).fill("power semiconductor");
  const go = sheet.getByRole("button", { name: "取得する" });
  await expect(go).toBeDisabled();
  await sheet.getByLabel(/外部の検索サービスへ送られます/).check();
  await expect(go).toBeEnabled();
  await page.keyboard.press("Escape");

  // 鍵の設定: 値は出さず「未設定 / 設定済み」だけ
  await page.getByRole("button", { name: "取得先の設定" }).click();
  const keys = page.getByRole("dialog");
  await expect(keys.getByLabel("EPO Consumer Key")).toHaveAttribute("type", "password");
  await expect(keys.getByLabel("USPTO API キー")).toBeVisible();
  await expect(keys.getByText("未設定").first()).toBeVisible();
  await page.screenshot({ path: "test-results/patents-source-keys-1280.png" });
});

// ---- 差し替える応答（本物の形: backend/app/patents/analytics.py） ----
const APPLICANTS = ["パワー電機株式会社", "東邦インバータ工業", "Northwind Power Systems Inc.", "日本半導体デバイス", "株式会社グリッドテック",
  "三和モータ", "Contoso Electric GmbH", "大和パワエレ研究所", "旭制御システム", "北辰電子", "瑞穂エナジー", "Fabrikam Semiconductor",
  "青葉電機", "白鷺工業", "富岳デバイス"];
const IPCS = ["H02M", "H01L", "H03K", "H02P", "G05F", "H02J", "B60L", "H05K"];
const YEARS = [2012, 2013, 2014, 2015, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];
const ANALYTICS = {
  documents: 120,
  coverage: { year: 112, applicant: 104, ipc: 98, claims: 87 },
  by_year: YEARS.map((y, i) => ({ key: String(y), count: [3, 4, 6, 5, 7, 9, 8, 12, 14, 13, 11, 10, 10][i] })),
  applicants: APPLICANTS.map((a, i) => ({ key: a, count: Math.max(1, 22 - i * 1.5 | 0) })),
  ipc_subclasses: IPCS.map((k, i) => ({ key: k, count: [41, 28, 19, 15, 9, 7, 5, 3][i] })),
  countries: [{ key: "JP", count: 74 }, { key: "US", count: 21 }, { key: "WO", count: 11 }, { key: "EP", count: 8 }, { key: "CN", count: 4 }, { key: "DE", count: 2 }],
  kinds: [{ key: "A", count: 82 }, { key: "B", count: 30 }, { key: "U", count: 8 }],
  applicant_ipc: APPLICANTS.slice(0, 8).flatMap((a, i) => IPCS.map((s, j) => ({ applicant: a, ipc: s, count: (i * 3 + j * 5) % 7 === 0 ? 0 : Math.max(0, 9 - i - j + ((i + j) % 3)) })))
    .filter((r) => r.count > 0),
  applicant_year: APPLICANTS.slice(0, 8).flatMap((a, i) => YEARS.map((y, j) => ({ applicant: a, year: String(y), count: (i + j) % 4 === 0 ? 0 : ((i * 7 + j * 3) % 5) })))
    .filter((r) => r.count > 0),
  claims_histogram: [{ key: "1–5", count: 18 }, { key: "6–10", count: 31 }, { key: "11–15", count: 20 }, { key: "16–20", count: 11 }, { key: "21–30", count: 5 }, { key: "31–", count: 2 }],
  note: "取り込んだ公報だけの集計です。選び方によって偏るので、業界全体の傾向とは限りません。書誌が読めていない公報は数えていません。",
};
const MAP = {
  model: "ruri-v3-310m",
  explained: [0.21, 0.12],
  points: Array.from({ length: 60 }, (_, i) => {
    const c = i % 5, a = (i * 2.399) % (Math.PI * 2), r = 0.05 + ((i * 37) % 11) / 60;
    const centers = [[-0.4, 0.2], [0.3, 0.35], [0.1, -0.3], [-0.2, -0.25], [0.45, -0.05]];
    return {
      ref: `patents:${i + 1}`, title: `ゲート駆動回路 ${i + 1}`,
      x: centers[c][0] + Math.cos(a) * r, y: centers[c][1] + Math.sin(a) * r,
      applicant: APPLICANTS[i % 8], ipc: i % 13 === 0 ? "" : IPCS[(c + (i % 3 === 0 ? 1 : 0)) % 7], year: String(YEARS[i % YEARS.length]),
    };
  }),
};
const DOCUMENT = {
  ref: "patents:1", title: "ゲート駆動回路 1", collection: "patents", collection_kind: "patents", source: "", url: "",
  sensitivity: "confidential", basis: "", ai_use: "allowed", text: "【発明の名称】ゲート駆動回路\n【要約】スイッチング損失を減らす。", text_truncated: false,
  chunk_count: 1, meta: { biblio: { applicants: ["パワー電機株式会社"], ipc: ["H02M 1/08", "H03K 17/16"], filing_date: "2021-04-02",
    publication_date: "2022-10-14", claims_count: 8, country: "JP", kind: "A", source: "ocr_extract" } },
  identifiers: [], references: [], assets: [], annotations: [], analysis_kinds: {}, related: [],
};
const HITS = [
  { id: 9001, watch_id: 1, doc_ref: "patents:901", title: "インバータのコモンモード雑音を抑える回路", score: 0.82, status: "new", created: 1795000000 },
  { id: 9002, watch_id: 1, doc_ref: "patents:902", title: "Common-mode noise suppression in multilevel inverters with an extremely long title that must be cut", score: 0.77, status: "new", created: 1794990000 },
  { id: 9003, watch_id: 1, doc_ref: "patents:903", title: "電力変換装置のゲート駆動", score: 0.7, status: "relevant", created: 1794900000 },
  { id: 9004, watch_id: 1, doc_ref: "patents:904", title: "モータ駆動装置", score: 0.6, status: "registered", created: 1794800000 },
  { id: 9005, watch_id: 1, doc_ref: "patents:905", title: "無関係な照明装置", score: 0.3, status: "excluded", created: 1794700000 },
];
