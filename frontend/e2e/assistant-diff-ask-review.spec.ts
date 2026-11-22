// 目的: (U7) 差分の行（会話の「変更」・作業場の結果の「変更」）を押すと小さな入力が開き、送ると
//       「<ファイル>:<行> の <その行の内容> について: <文>」が同じ会話へビルド（code_agent: build）で送られること。
//       完了カードと作業場の結果のカードの「レビューして」が短い指示（reviewer の係・問題があれば直す）を同じ会話へ送ること。
//       生成中は行も「レビューして」も出ないこと。320・1280、ライト・ダークで横にはみ出さないこと。
// 根拠: 2026-11-23 利用者の通し（評価 U7）。差分を見て気づいた所を、文でファイルと行を説明して頼んでいた。
//       送る口は「推奨どおりで進める」と同じ send。API は page.route で模擬する（OpenCode と LLM を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { askMessage, unifiedLineNumbers } from "../src/features/workflows/DiffAsk";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261123-1";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const REVIEW = "今の変更をレビューして（reviewer の係を使って）。問題があれば直して。";

test("unifiedLineNumbers / askMessage: new-side numbers, deleted lines use old numbers, long content is cut", () => {
  const nums = unifiedLineNumbers(["@@ -10,2 +10,3 @@", " 文脈", "-古い行", "+新しい行", "\\ No newline"]);
  expect(nums).toEqual([{ line: 10, deleted: false }, { line: 10, deleted: false }, { line: 11, deleted: true }, { line: 11, deleted: false }, null]);
  expect(unifiedLineNumbers(["diff --git a b", "+x"])).toEqual([null, null]);   // @@ より前は行番号を付けない
  expect(askMessage({ file: "a.ts", line: 3, content: "  x  " }, " 直して ")).toBe("a.ts:3 の x について: 直して");
  expect(askMessage({ file: "a.ts", line: 3, content: "y".repeat(100) }, "z")).toBe(`a.ts:3 の ${"y".repeat(79)}… について: z`);
});

const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}, status = "completed") => ({
  id, role, content, thinking: "", status, job_id: status === "generating" ? "j" : null, model: "", error: "", meta, created_at: "2026-11-23T00:00:00Z",
});

async function mock(page: Page, conv: string, messages: object[], generating = false) {
  const sent: Record<string, unknown>[] = [];
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(new RegExp(`/api/v1/chat/conversations/${conv}/messages$`), (route) => route.fulfill({ json: { conversation: { id: conv, title: "差分" }, messages } }));
  await page.route(new RegExp(`/api/v1/chat/conversations/${conv}/queue$`), (route) => route.fulfill({ json: { items: [], generating_message_id: generating ? "a" : null, held: false } }));
  await page.route(new RegExp(`/api/v1/chat/conversations/${conv}/send$`), (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 201, json: { user_message_id: "u1", assistant_message_id: "a1", job_id: "j1", status: "generating" } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, () => undefined);
  await page.evaluate((id) => localStorage.setItem("cd-chat-conversation", id), conv);
  return sent;
}

const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`changes list: ask about a line, review from the done card (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      const change = { file: "src/a.tsx", additions: 1, deletions: 1, status: "modified" };
      const sent = await mock(page, "c1", [msg("u", "user", "直して"),
        msg("a", "assistant", "直しました", { mode: "code", project_path: "/CodeDEV/app", opencode_session: "ses_1", agent: "build", changes: [change] })]);
      await page.route(/\/opencode\/changes\?/, (route) => route.fulfill({ json: { files: [{ ...change, patch: "diff --git a b\n@@ -10,2 +10,3 @@\n 文脈\n-古い行\n+新しい行\n" }] } }));
      await page.goto("/assistant");
      const card = page.locator("[data-code-changes]");
      await card.getByRole("button", { name: /変更 1 ファイル/ }).click();
      await card.getByRole("button", { name: /a\.tsx/ }).click();
      await card.locator("[data-code-patch] [data-diff-ask-row]", { hasText: "新しい行" }).click();
      const form = card.locator("[data-diff-ask-form]");
      await expect(form).toContainText("src/a.tsx:11");
      await noOverflow(page);
      await form.getByRole("textbox").fill("名前を直して");
      await page.screenshot({ path: `test-results/diff-ask-changes-${width}-${scheme}.png` });
      await form.locator("[data-diff-ask-send]").click();
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0]).toMatchObject({ mode: "code", code_agent: "build", content: "src/a.tsx:11 の 新しい行 について: 名前を直して" });
      await expect(form).toHaveCount(0);

      await page.goto("/assistant");   // 送ると生成中になりボタンは隠れる（意図どおり）。読み直して押す
      await page.locator("[data-code-done] [data-review-ask]").click();
      await expect.poll(() => sent.length).toBe(2);
      expect(sent[1]).toMatchObject({ mode: "code", code_agent: "build", content: REVIEW });
      await page.screenshot({ path: `test-results/diff-ask-done-${width}-${scheme}.png` });
    });

    test(`workspace result card: ask about a diff line, review (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      const sent = await mock(page, "w1", [msg("u0", "user", "直して", { mode_choice: "code", agent_choice: "build" }),
        msg("a0", "assistant", "直しました", { mode: "code", project_path: REPO, agent: "build" })]);
      await page.route(/\/api\/v1\/opencode\/workspaces$/, (route) => route.fulfill({ json: { workspaces: [{
        name: NAME, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${NAME}`,
        dirty: false, unpushed: 1, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
      await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
        base: "abc1234567", head: "def7654321", file_count: 1, added: 1, removed: 1, dirty: false,
        files: [{ path: "backend/app/x.py", added: 1, removed: 1, binary: false, untracked: false }],
        commits: [{ sha: "def7654321", author: "ControlDeck Sandbox", at: 1, subject: "x を直す" }], commit_count: 1, check: null, adopted: null } }));
      await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => route.fulfill({ json: { check: null, running: false, job_id: null } }));
      await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/diff(\\?.*)?$`), (route) => {
        const path = new URL(route.request().url()).searchParams.get("path") ?? "";
        const diff = "diff --git a/backend/app/x.py b/backend/app/x.py\n--- a/backend/app/x.py\n+++ b/backend/app/x.py\n@@ -3,3 +3,3 @@ def f():\n keep one\n-removed line\n+added line\n";
        return route.fulfill({ json: { base: "abc", head: "def", limit: 262144, uncommitted: null,
          files: [{ path: "backend/app/x.py", added: 1, removed: 1, binary: false }],
          file: path ? { path, binary: false, truncated: false, diff, size: diff.length } : null } });
      });
      await page.goto("/assistant");
      const card = page.locator('[data-testid="workspace-result"]');
      await card.getByRole("button", { name: /作業場の結果/ }).click();
      await card.getByTestId("workspace-diff-toggle").click();
      await card.getByRole("button", { name: /backend\/app\/x\.py/ }).click();
      await card.locator('[data-diff="add"]').click();
      const form = card.locator("[data-diff-ask-form]");
      await expect(form).toContainText("backend/app/x.py:4");
      await form.getByRole("textbox").fill("定数にして");
      await noOverflow(page);
      await page.screenshot({ path: `test-results/diff-ask-workspace-${width}-${scheme}.png` });
      await form.getByRole("textbox").press("Enter");
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0]).toMatchObject({ mode: "code", code_agent: "build", content: "backend/app/x.py:4 の added line について: 定数にして" });

      await page.goto("/assistant");
      await card.getByRole("button", { name: /作業場の結果/ }).click();
      await card.locator("[data-review-ask]").click();
      await expect.poll(() => sent.length).toBe(2);
      expect(sent[1]).toMatchObject({ mode: "code", code_agent: "build", content: REVIEW });
    });
  }
}

test("no ask rows or review button while the assistant is generating", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await mock(page, "w1", [msg("u0", "user", "直して", { mode_choice: "code", agent_choice: "build" }),
    msg("a", "assistant", "", { mode: "code", project_path: REPO, agent: "build" }, "generating")], true);
  await page.route(/\/api\/v1\/opencode\/workspaces$/, (route) => route.fulfill({ json: { workspaces: [{
    name: NAME, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${NAME}`,
    dirty: false, unpushed: 1, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
    base: "a", head: "b", file_count: 1, added: 1, removed: 0, dirty: false, files: [{ path: "x.py", added: 1, removed: 0, binary: false, untracked: false }],
    commits: [], commit_count: 0, check: null, adopted: null } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => route.fulfill({ json: { check: null, running: false, job_id: null } }));
  await page.goto("/assistant");
  const card = page.locator('[data-testid="workspace-result"]');
  await card.getByRole("button", { name: /作業場の結果/ }).click();
  await expect(card.getByRole("button", { name: /変わったファイル 1 件/ })).toBeVisible();
  await expect(page.locator("[data-review-ask]")).toHaveCount(0);
});
