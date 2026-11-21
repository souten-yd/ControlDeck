import { expect, test, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { establishSession, hasSession } from "./support/session";

/** Patent と Knowledge のボタンを、入力を入れて押し、結果まで確かめる（検証用インスタンス・1280px）。
 *  根拠: 2026-11-21 利用者「各ボタンなどの機能動作も確認して」。
 *  検証用インスタンスにはローカルのモデル・埋め込みが無いので、AI のボタンは「押せない理由が出ること」までを確かめる
 *  （AI の実行結果は本番の評価 ~/ControlDeck-評価/ で確かめる）。外部へ送るボタン（取得する・いま取得）は押さない。
 *  押したボタンと結果の一覧は test-results/button-sweep.json に書く。 */

test.use({ actionTimeout: 10_000 });

interface Row { area: string; button: string; ok: boolean; note: string }
const rows: Row[] = [];
const problems: string[] = [];
let page0: Page | undefined;

async function check(area: string, button: string, fn: () => Promise<string | void>) {
  try {
    const note = await fn();
    rows.push({ area, button, ok: true, note: note ?? "" });
  } catch (e) {
    rows.push({ area, button, ok: false, note: String(e).split("\n").slice(0, 3).join(" ").slice(0, 400) });
    await page0?.keyboard.press("Escape").catch(() => {});
  }
  writeFileSync("test-results/button-sweep.json", JSON.stringify({ rows, problems }, null, 1));
}

function watchPage(page: Page) {
  page.on("pageerror", (e) => problems.push(`画面の例外: ${e.message.slice(0, 200)}`));
  page.on("response", (r) => { if (r.status() >= 500) problems.push(`サーバー ${r.status()}: ${r.request().method()} ${new URL(r.url()).pathname}`); });
}

test("Patent と Knowledge のボタンの動作（1280px）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  test.setTimeout(600_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  watchPage(page);
  page0 = page;
  const toast = (text: string | RegExp) => expect(page.getByText(text).first()).toBeVisible({ timeout: 10_000 });
  const pane = page.locator('section[aria-labelledby="patent-section-title"]');
  const open = async (name: string) => {
    await page.getByRole("button", { name, exact: true }).click();
    await expect(pane.getByRole("heading", { level: 2, name })).toBeVisible();
  };

  // ---- Knowledge: 資料を用意（コレクション作成 → テキスト取り込み）----
  await page.goto("/knowledge");
  await check("Knowledge / 資料の管理", "コレクション作成 → 作成", async () => {
    await page.getByRole("button", { name: "資料の管理", exact: true }).first().click();
    await page.getByRole("button", { name: /コレクション作成|作成/ }).first().click();
    const sheet = page.getByRole("dialog");
    await sheet.getByPlaceholder("my-docs").fill("sweep-docs");
    await sheet.getByRole("button", { name: /^作成/ }).last().click();
    await expect(page.getByText("sweep-docs").first()).toBeVisible({ timeout: 10_000 });
  });
  await check("Knowledge / 資料の管理", "コレクションを開く → テキストを取り込む", async () => {
    await page.getByRole("button", { name: /sweep-docs/ }).first().click();
    const sheet = page.getByRole("dialog");
    await sheet.getByRole("button", { name: "取り込み", exact: true }).click();
    await sheet.getByRole("button", { name: "テキスト", exact: true }).click();
    await sheet.getByPlaceholder("取り込むテキスト").fill("窒化ガリウムのスイッチング素子と、ゲートを駆動する制御部と、を備えるインバータ。冷却器は備えない。");
    await sheet.getByRole("button", { name: /^取り込/ }).last().click();
    await expect(page.getByText(/チャンクを取り込みました|取り込みに失敗/).first()).toBeVisible({ timeout: 60_000 });
    const note = (await page.getByText(/チャンクを取り込みました/).count()) ? "取り込めた" : `失敗: ${await page.getByText(/失敗/).first().textContent()}`;
    await sheet.getByRole("button", { name: "文書", exact: true }).click();
    await page.keyboard.press("Escape");
    return note;
  });

  await check("Knowledge / 探す", "探す（言葉）", async () => {
    await page.getByRole("button", { name: "探す", exact: true }).first().click();
    await page.getByPlaceholder(/探す|言葉/).first().fill("窒化ガリウム");
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await expect(page.getByText(/インバータ|見つかりません|ありません/).first()).toBeVisible({ timeout: 10_000 });
    return (await page.getByText("インバータ").count()) ? "取り込んだ資料が見つかった" : "見つからなかった";
  });
  await check("Knowledge / 探す", "結果を開く（文書のシート）→ 全文を表示", async () => {
    await page.getByText(/インバータ/).first().click();
    const sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    const full = sheet.getByRole("button", { name: /全文を表示/ });
    if (await full.count()) await full.click();
    const ai = sheet.getByRole("button", { name: /を作る/ }).first();
    const note = (await ai.count()) ? `AI 分析のボタン: ${(await ai.isDisabled()) ? "押せない（ローカルのモデル無し）" : "押せる"}` : "AI 分析のボタン無し";
    await page.keyboard.press("Escape");
    return note;
  });
  await check("Knowledge / 資料の管理", "作り直し（計画を見る → 作り直す）", async () => {
    await page.getByRole("button", { name: "資料の管理", exact: true }).first().click();
    await page.getByRole("button", { name: "作り直し", exact: true }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText(/作り直す|ありません|件/).first()).toBeVisible({ timeout: 10_000 });
    const run = sheet.getByRole("button", { name: "作り直す", exact: true });
    const note = (await run.count()) && !(await run.isDisabled()) ? "作り直す対象あり" : "作り直す対象なし（押せない）";
    if (note !== "作り直す対象あり") { await page.keyboard.press("Escape"); return note; }
    const plan = await sheet.textContent();
    await run.click();
    await toast("作り直しを始めました");
    await page.waitForTimeout(5000);
    const jobs = await (await page.request.get("/api/v1/jobs?limit=5")).json();
    const job = (Array.isArray(jobs) ? jobs : jobs.jobs ?? []).find((j: { kind: string }) => j.kind.includes("rebuild"));
    return `計画: ${(plan ?? "").replace(/\s+/g, " ").slice(0, 120)} → Job ${job?.status ?? "?"}${job?.error ? `（${job.error.slice(0, 80)}）` : ""}`;
  });

  // ---- Patent: 案件 ----
  await page.goto("/patents");
  await check("Patent 一覧", "案件を作成 → 作成", async () => {
    await page.getByRole("button", { name: "案件を作成" }).first().click();
    await page.getByPlaceholder("発明の名称").fill("E2E ボタン確認");
    await page.getByRole("button", { name: "作成", exact: true }).click();
    await expect(page).toHaveURL(/\/patents\/\d+$/);
  });
  await check("案件 / 概要", "名称・段階・課題を直して保存", async () => {
    await pane.getByLabel("発明の名称").fill("E2E ボタン確認（改）");
    await pane.getByLabel("段階").selectOption({ index: 2 });
    await pane.getByLabel("課題", { exact: true }).fill("スイッチング損失が大きい");
    await pane.getByRole("button", { name: "保存", exact: true }).click();
    await toast("保存しました");
    await expect(page.getByRole("heading", { name: "E2E ボタン確認（改）" })).toBeVisible();
  });
  await check("案件 / 右の欄", "期限を開く・AI 支援を使う", async () => {
    await page.getByRole("button", { name: "期限を開く" }).click().catch(() => page.getByRole("link", { name: "期限を開く" }).click());
    await expect(pane.getByRole("heading", { level: 2, name: "期限" })).toBeVisible();
    const assist = page.getByRole("button", { name: "AI 支援を使う" });
    if (await assist.count()) { await assist.click(); await expect(pane.getByRole("heading", { level: 2, name: "AI 支援" })).toBeVisible(); }
  });
  await check("案件 / ヒアリング", "直した内容を保存", async () => {
    await open("ヒアリング");
    await pane.getByPlaceholder("話した内容がここに入ります。手で書き足したり直したりもできます。").fill("インバータの損失を減らしたい。GaN を使う。");
    await pane.getByRole("button", { name: "直した内容を保存" }).click();
    await expect(pane.getByText("文字", { exact: false }).first()).toBeVisible();
  });
  await check("案件 / ヒアリング", "AI で本質と請求項に整理", async () => {
    const b = pane.getByRole("button", { name: "AI で本質と請求項に整理" });
    return (await b.isDisabled()) ? "押せない（ローカルのモデル無し。期待どおり）" : "押せる";
  });
  await check("案件 / ヒアリング", "話して記録する（マイク）", async () => "押さない（マイクを使うため。本番で利用者が確認）");
  await check("案件 / 期限", "出来事を記録 → 削除", async () => {
    await open("期限");
    await pane.locator('input[type="date"]').fill("2026-01-31");
    await pane.getByRole("button", { name: "出来事を記録" }).click();
    await expect(pane.getByText("審査請求期限（出願日から3年）")).toBeVisible();
    await pane.getByRole("button", { name: "削除" }).first().click();
    await expect(pane.getByText("審査請求期限（出願日から3年）")).toHaveCount(0);
    await pane.locator('input[type="date"]').fill("2026-01-31");
    await pane.getByRole("button", { name: "出来事を記録" }).click();
    await expect(pane.getByText("審査請求期限（出願日から3年）")).toBeVisible();
  });
  await check("案件 / 請求項", "新しい版で保存（2 版）", async () => {
    await open("請求項");
    await pane.locator("textarea").first().fill("【請求項１】\nスイッチング素子と、制御部と、を備えるインバータ。");
    await pane.getByRole("button", { name: "新しい版で保存" }).click();
    await expect(pane.getByText("第 1 版（全 1 版）")).toBeVisible();
    await pane.locator("textarea").first().fill("【請求項１】\nスイッチング素子と、制御部と、冷却器と、を備えるインバータ。");
    await pane.getByRole("button", { name: "新しい版で保存" }).click();
    await expect(pane.getByText("第 2 版（全 2 版）")).toBeVisible();
  });
  await check("案件 / 請求項", "クレームチャートを作る", async () => {
    await pane.getByRole("button", { name: "クレームチャートを作る" }).click();
    await expect(pane.getByText("構成要件ごとにナレッジ", { exact: false })).toBeVisible({ timeout: 15_000 });
    return (await pane.getByText("候補なし").count()) === 3 ? "候補なし（資料に当たらず）" : "候補が出た";
  });
  await check("案件 / 請求項", "請求項 1 を判定（AI）", async () => {
    const b = pane.getByRole("button", { name: /請求項 1 を判定/ });
    if (!(await b.count())) return "判定する文献が無いので出ない（候補なし）";
    return (await b.isDisabled()) ? "押せない（ローカルのモデル無し。期待どおり）" : "押せる";
  });
  await check("案件 / 文献", "ナレッジを検索 → 先行文献に追加（手入力）→ 削除", async () => {
    await open("文献");
    await pane.getByPlaceholder("ナレッジを検索（一般・特許・規格）").fill("インバータ");
    await pane.getByRole("button", { name: "検索" }).click();
    await page.waitForTimeout(1500);
    const hits = await pane.getByRole("button", { name: /登録|追加/ }).count();
    await pane.getByPlaceholder("文献番号を直接入れる（例: 特開2026-116114）").fill("特開2026-000123");
    await pane.getByRole("button", { name: "追加", exact: true }).click();
    await expect(pane.getByText("特開2026-000123")).toBeVisible();
    await pane.getByRole("button", { name: "削除" }).first().click();
    await expect(pane.getByText("特開2026-000123")).toHaveCount(0);
    return `検索の結果の操作ボタン ${hits} 個`;
  });
  await check("案件 / ウォッチ", "ウォッチを追加（確認のチェック）→ 直して保存 → 削除", async () => {
    await open("ウォッチ");
    await pane.getByRole("button", { name: "ウォッチを追加" }).click();
    const sheet = page.getByRole("dialog");
    await sheet.getByRole("combobox").first().selectOption("openalex");
    await sheet.getByRole("textbox", { name: "検索語" }).fill("gan inverter switching loss");
    await sheet.getByLabel(/外部の検索サービスへ送られます/).check();
    await sheet.getByRole("button", { name: "追加", exact: true }).click();
    await toast("ウォッチを追加しました");
    await pane.getByRole("button", { name: "ウォッチの操作" }).first().click();
    await page.getByRole("menuitem", { name: "直す" }).click();
    const edit = page.getByRole("dialog");
    {
      await edit.getByRole("textbox", { name: "検索語" }).fill("gan inverter loss");
      await expect(edit.getByRole("button", { name: "保存", exact: true })).toBeDisabled();   // 語を変えたら確認をやり直す
      await edit.getByLabel(/外部の検索サービスへ送られます/).check();
      await edit.getByRole("button", { name: "保存", exact: true }).click();
      await expect(pane.getByText("gan inverter loss")).toBeVisible();
    }
    await pane.getByRole("button", { name: "ウォッチの操作" }).first().click();
    await page.getByRole("menuitem", { name: "止める" }).click();
    await expect(pane.getByText(/止めて|停止|再開/).first()).toBeVisible();
    await pane.getByRole("button", { name: "ウォッチの操作" }).first().click();
    await page.getByRole("menuitem", { name: "削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(pane.getByText(/gan inverter/)).toHaveCount(0);
    return "いま取得（外部へ送る）は押していない";
  });
  await check("案件 / AI 支援", "ローカル AI で作成", async () => {
    await open("AI 支援");
    const b = pane.getByRole("button", { name: "ローカル AI で作成" });
    await expect(pane.getByText("ローカル専用のモデルが使えないため実行できません", { exact: false })).toBeVisible();
    return (await b.isDisabled()) ? "押せない＋理由が出る（期待どおり）" : "押せる";
  });
  await check("案件 / 分析", "近い公報（もう一度探す）", async () => {
    await open("分析");
    const again = pane.getByRole("button", { name: "もう一度探す" });
    if (await again.count()) await again.click();
    await expect(pane.getByText(/に近い公報です|似た公報を探せません/).first()).toBeVisible({ timeout: 10_000 });
  });

  // ---- Patent: 一覧の分析・公報 ----
  await check("Patent / 分析", "もう一度読み込む・書誌を読み直す", async () => {
    await page.goto("/patents?tab=analysis");
    await page.waitForTimeout(800);
    const reread = page.getByRole("button", { name: "書誌を読み直す" });
    if (await reread.count()) { await reread.click(); await toast(/始めました|読み直/); }
    const again = page.getByRole("button", { name: "もう一度読み込む" });
    if (await again.count()) await again.click();
    return `書誌を読み直す ${await reread.count() ? "押した" : "無し（公報 0 件）"}`;
  });
  await check("Patent / 公報", "公報・論文を取り込む（本文を貼る）", async () => {
    await page.goto("/patents?tab=docs");
    await page.getByRole("button", { name: "公報・論文を取り込む" }).click();
    const sheet = page.getByRole("dialog");
    await sheet.getByPlaceholder("題名・公報番号").fill("特開2026-999999 インバータ");
    await sheet.getByPlaceholder("本文", { exact: true }).fill("【請求項１】スイッチング素子と制御部と冷却器とを備えるインバータ。【０００１】本発明は冷却に関する。");
    await sheet.getByRole("button", { name: "取り込む", exact: true }).click();
    await toast(/取り込み|始めました/);
  });
  await check("Patent / 公報", "探す（番号）", async () => {
    await page.waitForTimeout(3000);
    await page.getByLabel("公報を探す").fill("特開2026-999999");
    await page.getByRole("button", { name: "探す", exact: true }).click();
    await expect(page.getByText(/特開2026-999999|見つかりません|ありません/).first()).toBeVisible({ timeout: 10_000 });
    return (await page.getByText("特開2026-999999 インバータ").count()) ? "取り込んだ公報が見つかった" : "見つからない";
  });
  await check("Patent / 公報", "取得先の設定（シートを開く・保存は変更が無ければ押せない）", async () => {
    await page.getByRole("button", { name: "取得先の設定" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");
  });
  await check("Patent / 公報", "公開データを取得（確認のチェックが要る・取得するは押さない）", async () => {
    await page.getByRole("button", { name: "公開データを取得" }).click();
    const sheet = page.getByRole("dialog");
    const get = sheet.getByRole("button", { name: "取得する" });
    await sheet.locator('input[type="text"], input:not([type])').last().fill("gan inverter");
    await expect(get).toBeDisabled();
    await page.keyboard.press("Escape");
  });

  // ---- 後片付け: 案件を削除（⋯ → 確認）・コレクションを削除 ----
  await check("案件", "⋯ → 案件を削除", async () => {
    await page.goto("/patents");
    await page.getByRole("link", { name: /E2E ボタン確認/ }).first().click();
    await page.getByRole("button", { name: "案件のその他の操作" }).click();
    await page.getByRole("menuitem", { name: "案件を削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(page).toHaveURL(/\/patents$/);
  });
  await check("Knowledge / 資料の管理", "コレクションを削除", async () => {
    await page.goto("/knowledge");
    await page.getByRole("button", { name: "資料の管理", exact: true }).first().click();
    await page.locator("li", { hasText: "sweep-docs" }).getByRole("button", { name: "削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(page.getByText("sweep-docs")).toHaveCount(0, { timeout: 10_000 });
  });

  writeFileSync("test-results/button-sweep.json", JSON.stringify({ rows, problems }, null, 1));
  const failed = rows.filter((r) => !r.ok);
  expect([...failed.map((r) => `${r.area} / ${r.button}: ${r.note}`), ...problems]).toEqual([]);
});
