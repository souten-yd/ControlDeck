import { expect, test } from "@playwright/test";
import { emptyCloud, mockCloud } from "./support/cloudModels";
import { establishSession, hasSession } from "./support/session";

/** Models の「フロンティアモデル」: キーを保存 → 有効 → モデルを選ぶ → 外部への送信を許可、を 320px で通す。
 *
 * API は page.route で差し替える。本物の設定には書かず、外部へも送らない。 */
const KEY = "sk-or-e2e-secret-value-9f3c";

test("キーを保存して有効にし、モデルを選び、確認のうえで外部への送信を許可できる（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await page.setViewportSize({ width: 320, height: 700 });
  const state = emptyCloud();
  const puts = await mockCloud(page, state);
  await establishSession(page, context);
  await page.goto("/models");

  // ローカルのモデルとは別のタブ。開くと「送信されない」と言い切る。
  await page.getByRole("button", { name: "フロンティア（外部）" }).click();
  await expect(page).toHaveURL(/tab=frontier/);
  // タブの中はフロンティアモデルだけなので、ページ全体を範囲にする。
  const group = page.locator("body");
  await expect(page.getByText("いまは外部へ何も送信されません")).toBeVisible();
  await expect(group.getByText(/Codex CLI \/ Claude Code に機器認証でログイン/)).toBeVisible();

  const card = group.locator("li", { has: page.getByRole("button", { name: /^OpenRouter/ }) });
  await expect(card.getByText("未設定")).toBeVisible();
  await card.getByRole("button", { name: /^OpenRouter/ }).click();
  const link = card.getByRole("link", { name: /キーを取得/ });
  await expect(link).toHaveAttribute("href", "https://openrouter.ai/settings/keys");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");

  // キーが無いあいだは有効にできない。
  const enable = card.getByRole("checkbox", { name: /このプロバイダを有効にする/ });
  await expect(enable).toBeDisabled();
  const keyInput = card.getByLabel("API キー", { exact: true });
  await expect(keyInput).toHaveAttribute("type", "password");
  await expect(keyInput).toHaveAttribute("autocomplete", "off");
  await keyInput.fill(KEY);
  await card.getByRole("button", { name: "保存", exact: true }).first().click();

  // 保存後は末尾 4 文字だけ。入力欄もキーも画面に残らない。
  await expect(card.getByText("••••9f3c")).toBeVisible();
  await expect(card.getByLabel("API キー", { exact: true })).toHaveCount(0);
  expect(await page.content()).not.toContain(KEY);
  expect(puts[0]).toEqual({ path: "openrouter", body: { api_key: KEY } });
  await expect(card.getByText("無効", { exact: true })).toBeVisible();

  await expect(enable).toBeEnabled();
  // 保存が済んでから表示が変わる（楽観的には変えない）ので check() ではなく click()。
  await enable.click();
  await expect(enable).toBeChecked();
  await expect(card.getByText("有効", { exact: true })).toBeVisible();

  // モデルを選ぶ: 数千件でも先頭 100 件だけ描き、検索と「無料だけ」で絞れる。
  await card.getByRole("button", { name: "モデルを選ぶ" }).click();
  const sheet = page.getByRole("dialog", { name: "OpenRouter のモデルを選ぶ" });
  await expect(sheet.getByRole("listitem")).toHaveCount(100);
  await sheet.getByRole("button", { name: /さらに表示/ }).click();
  await expect(sheet.getByRole("listitem")).toHaveCount(200);
  await sheet.getByRole("searchbox").fill("claude");
  await expect(sheet.getByRole("listitem")).toHaveCount(1);
  await sheet.getByRole("checkbox", { name: /anthropic\/claude-test/ }).check();
  await sheet.getByRole("searchbox").fill("");
  await sheet.getByRole("checkbox", { name: "無料だけ" }).check();
  await expect(sheet.getByRole("listitem")).toHaveCount(31);
  await sheet.getByRole("checkbox", { name: /meta\/llama-test:free/ }).check();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await sheet.getByRole("button", { name: "2 件を保存" }).click();
  await expect(sheet).toBeHidden();
  expect(puts.at(-1)).toEqual({ path: "openrouter", body: { models: ["anthropic/claude-test", "meta/llama-test:free"] } });

  // 選んだモデルはゲートウェイでの名前で並び、1 つずつ外せる。
  const chosen = card.getByRole("list", { name: "OpenRouter で使うモデル" });
  await expect(chosen.getByText("cloud:openrouter/anthropic/claude-test")).toBeVisible();
  await expect(chosen.getByRole("button", { name: "cloud:openrouter/meta/llama-test:free をコピー" })).toBeVisible();
  await chosen.getByRole("button", { name: "meta/llama-test:free を外す" }).click();
  await expect(chosen.getByRole("listitem")).toHaveCount(1);

  // 外部への送信: 既定はすべてオフ。オンにするときだけ確かめ、やめれば何も送らない。
  const opencode = group.getByRole("checkbox", { name: /^OpenCode/ });
  for (const name of [/^OpenCode/, /^AI アシスタント/, /^Workflow/, /^ゲートウェイを直接使う外部クライアント/]) {
    await expect(group.getByRole("checkbox", { name })).not.toBeChecked();
  }
  await expect(group.getByText(/PatentBuddy など）は、この設定に関係なく外部へ送信されません/)).toBeVisible();
  await opencode.click();
  const confirm = page.getByRole("alertdialog", { name: "OpenCode から外部へ送信できるようにしますか？" });
  await expect(confirm).toContainText("そのプロバイダへ送信されます");
  await confirm.getByRole("button", { name: "キャンセル" }).click();
  await expect(opencode).not.toBeChecked();
  expect(puts.some((put) => put.path === "egress")).toBe(false);

  await opencode.click();
  await confirm.getByRole("button", { name: "送信を許可する" }).click();
  await expect(opencode).toBeChecked();
  expect(puts.at(-1)).toEqual({ path: "egress", body: { opencode: true } });
  await expect(group.getByText("外部へ送信できる状態です")).toBeVisible();
  await expect(group.getByRole("status")).toContainText("OpenCode → OpenRouter");

  // オフは確認なしですぐ効く。
  await opencode.click();
  await expect(opencode).not.toBeChecked();
  await expect(group.getByText("いまは外部へ何も送信されません")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("OpenRouter の費用・入力の扱い・残高が見え、並べ替えと絞り込みができる（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await page.setViewportSize({ width: 320, height: 700 });
  const state = emptyCloud();
  Object.assign(state.providers[0], { configured: true, key_hint: "9f3c", enabled: true, models: ["anthropic/claude-test"] });
  const puts = await mockCloud(page, state);
  await establishSession(page, context);
  await page.goto("/models?tab=frontier");

  // 設定済みのカードは開いている。見出しに、いまの「データの扱い」がアイコンと文字で出る。
  const card = page.locator("li", { has: page.getByRole("button", { name: /^OpenRouter/ }) });
  await expect(card.getByRole("button", { name: /^OpenRouter/ })).toContainText("学習する提供元を除外");

  // 残高は OpenRouter 側の数字。ControlDeck の上限とは別のものとして並ぶ。
  const balance = card.getByRole("region", { name: "OpenRouter の残高" });
  await expect(balance).toContainText("$12.50");
  await expect(balance).toContainText("上限 $20.00");
  await expect(balance).toContainText("使用額 今月$7.50");
  await expect(balance).toContainText("使用額 今日$0.4200");
  await expect(balance).toContainText("使用額 累計$31.25");
  await expect(balance).toContainText("今日はあと 950 回");
  await expect(balance.getByRole("button", { name: "残高を更新（OpenRouter）" })).toBeEnabled();
  await expect(card.getByLabel("ControlDeck の月の上限（USD）")).toBeVisible();

  // 選んだモデルにも費用と入力の扱いが付く。
  const chosen = card.getByRole("list", { name: "OpenRouter で使うモデル" });
  await expect(chosen.getByRole("listitem")).toContainText("$3 / $15");
  await expect(chosen.getByRole("listitem")).toContainText("保存しない経路あり");

  // データの扱い: 緩める（制限しない）ときだけ確かめる。やめれば何も変わらない。
  const policy = card.getByRole("group", { name: "データの扱い" });
  await expect(policy.getByRole("radio", { name: /学習に使う提供元へは送らない/ })).toBeChecked();
  const allow = policy.getByRole("radio", { name: /^制限しない/ });
  await allow.click();
  const confirm = page.getByRole("alertdialog", { name: "データの扱いを「制限しない」にしますか？" });
  await expect(confirm).toContainText("学習に使ったりする提供元");
  await confirm.getByRole("button", { name: "キャンセル" }).click();
  await expect(allow).not.toBeChecked();
  expect(puts.some((put) => "data_policy" in put.body)).toBe(false);
  await allow.click();
  await confirm.getByRole("button", { name: "制限しない" }).click();
  await expect(allow).toBeChecked();
  expect(puts.at(-1)).toEqual({ path: "openrouter", body: { data_policy: "allow" } });
  await expect(card.getByRole("button", { name: /^OpenRouter/ })).toContainText("制限なし");

  // 厳しくするのは確認なしですぐ効く。
  const zdr = policy.getByRole("radio", { name: /保存しない提供元だけ/ });
  await zdr.click();
  await expect(zdr).toBeChecked();
  expect(puts.at(-1)).toEqual({ path: "openrouter", body: { data_policy: "zdr" } });

  // モデル選択: 並び順で先頭が変わる。
  await card.getByRole("button", { name: "モデルを選ぶ" }).click();
  const sheet = page.getByRole("dialog", { name: "OpenRouter のモデルを選ぶ" });
  const rows = sheet.getByRole("listitem");
  await expect(rows.first()).toContainText("anthropic/claude-test");
  await expect(rows.first()).toContainText("$3 / $15");
  await expect(rows.first()).not.toContainText("この設定では送られません");
  const sort = sheet.getByLabel("並び順");
  await sort.selectOption({ label: "安い順" });
  await expect(rows.first()).toContainText("meta/llama-test:free");
  await expect(rows.first()).toContainText("学習・保存されうる");
  // いまの設定（保存しない提供元だけ）では送られないモデルも、隠さずにそう書く。
  await expect(rows.first()).toContainText("この設定では送られません");
  await sort.selectOption({ label: "高い順" });
  await expect(rows.first()).toContainText("vendor-36/model-36");
  await expect(rows.first()).toContainText("$14.5 / $58");
  await sort.selectOption({ label: "新しい順" });
  await expect(rows.first()).toContainText("anthropic/claude-test");
  await sort.selectOption({ label: "コンテキストが長い順" });
  await expect(rows.first()).toContainText("anthropic/claude-test");

  // 保存しない経路があるものだけに絞る。
  await sheet.getByRole("checkbox", { name: "保存しない経路あり（ZDR）だけ" }).check();
  await expect(sheet.getByText(/^491 件/)).toBeVisible();
  await expect(rows).toHaveCount(100);
  await expect(rows.filter({ hasText: "この設定では送られません" })).toHaveCount(0);
  await sheet.getByRole("searchbox").fill("llama");
  await expect(rows).toHaveCount(0);

  // アイコンの見方は畳んであり、開けば読める。
  await sheet.getByText("アイコンの見方").click();
  await expect(sheet.getByText("プロバイダ自身の API。入力の扱いはそのプロバイダの規約によります")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
