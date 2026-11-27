// 目的: 返事の中のコマンド（bash などのシェルの言語・言語なしでコマンドに見えるブロック）に「▶ 実行」が出て、
//       確認のシート（内容・場所・危険な内容は赤字で 2 段）を通して ControlDeck の Terminal へ渡ることを守る。
//       python などシェルでない言語には出ない。場所はその会話の作業場（無ければホーム）。
// 根拠: 2026-11-27 利用者「返事の中のコマンドを、ボタン 1 つでコンソール（Terminal）で実行する」。
//       API は既存の POST /terminals（cwd を足した）だけ。新しい危ない API は作っていない。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261127-1";
const REPO = `/data/sandboxes/${NAME}/work/repo`;

const ANSWER = [
  "次のコマンドです。",
  "",
  "```bash",
  "echo hello",
  "```",
  "",
  "```",
  "ls",
  "pwd",
  "```",
  "",
  "```python",
  'print("x")',
  "```",
  "",
  "```bash",
  "sudo rm -rf /tmp/controldeck-e2e",
  "```",
].join("\n");

const msg = (id: string, role: "user" | "assistant", content: string, meta: Record<string, unknown> = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta,
  created_at: "2026-11-27T00:00:00Z",
});

async function openAssistant(page: Page) {
  const created: Record<string, unknown>[] = [];
  await page.route(/\/api\/v1\/opencode\/workspaces$/, (route) => route.fulfill({ json: { workspaces: [{
    name: NAME, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${NAME}`,
    dirty: false, unpushed: 0, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/run1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "run1", title: "実行" },
    messages: [msg("u1", "user", "調べて"),
      msg("a1", "assistant", ANSWER, { mode: "code", project_path: REPO, agent: "build" })],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/run1\/queue$/, (route) =>
    route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/terminals$/, (route) => {
    if (route.request().method() === "POST") {
      created.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ status: 201, json: { id: "term1", name: "term1", created_at: 1, attached: false,
        persistent: true, program: "bash", cwd: REPO, pid: 1, activity_at: 1, alive: true, workload: "idle", engine: "v1" } });
    }
    return route.fulfill({ json: { tmux: true, sessions: [{ id: "term1", name: "term1", created_at: 1, attached: false,
      persistent: true, program: "bash", cwd: REPO, pid: 1, activity_at: 1, alive: true, workload: "idle", engine: "v1" }] } });
  });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "run1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell).toBeVisible();
  return { shell, created };
}

for (const width of [390, 1280]) {
  test(`code blocks show run button only for shell commands (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 844 });
    await establishSession(page, context);
    const { shell } = await openAssistant(page);

    // bash 2 つと言語なし 1 つだけ。python には出ない
    const runs = shell.locator('[data-testid="code-run"]');
    await expect(runs).toHaveCount(3);
    expect(await runs.first().getAttribute("aria-label")).toBe("ターミナルで実行");

    // 安全なコマンド: シートに内容と場所（その会話の作業場）が出る
    await runs.nth(0).click();
    const sheet = page.getByRole("dialog", { name: "ターミナルで実行" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId("run-command-text")).toHaveText("echo hello");
    await expect(sheet.getByTestId("run-command-place")).toContainText(`作業場 ${NAME}`);
    await expect(sheet.getByTestId("run-command-place")).toContainText(REPO);
    await expect(sheet.getByTestId("run-command-warnings")).toHaveCount(0);
    await expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/code-run-${width}.png` });
    await sheet.getByRole("button", { name: "キャンセル" }).click();
    await expect(sheet).toBeHidden();
  });
}

test("dangerous command needs a red note and two steps, then runs in the workspace", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  const { shell, created } = await openAssistant(page);

  // 危険なコマンド: 赤字の注意と 2 段の確認
  await shell.locator('[data-testid="code-run"]').nth(2).click();
  const sheet = page.getByRole("dialog", { name: "ターミナルで実行" });
  await expect(sheet.getByTestId("run-command-text")).toHaveText("sudo rm -rf /tmp/controldeck-e2e");
  const warnings = sheet.getByTestId("run-command-warnings");
  await expect(warnings).toContainText("sudo");
  await expect(warnings).toContainText("rm -rf");
  await expect(warnings).toContainText("もう一度");
  const confirm = sheet.getByTestId("run-command-confirm");
  // 1 回目は用意だけ（まだ実行しない）
  await confirm.click();
  await expect(created).toHaveLength(0);
  // 2 回目で実行: 作業場の cwd 付きで既存のセッション作成 API を呼び、Terminal へ渡す
  await confirm.click();
  await expect.poll(() => created.length).toBe(1);
  expect(created[0]).toEqual({ cwd: REPO });
  await expect(page).toHaveURL(new RegExp(`/terminal\\?session=term1$`));
  const handed = await page.evaluate(() => JSON.parse(sessionStorage.getItem("control-deck:terminal-autorun") || "null"));
  expect(handed).toMatchObject({ sessionId: "term1", text: "sudo rm -rf /tmp/controldeck-e2e" });
  await page.screenshot({ path: "test-results/code-run-terminal.png" });
});
