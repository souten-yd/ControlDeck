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

  // 何も設定していなければ畳まれている。開くと「送信されない」と言い切る。
  const group = page.locator("details", { has: page.locator("summary", { hasText: "フロンティアモデル" }) });
  await expect(group).not.toHaveAttribute("open", /.*/);
  await group.locator("summary").first().click();
  await expect(group.getByText("いまは外部へ何も送信されません")).toBeVisible();
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
