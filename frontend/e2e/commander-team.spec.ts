/** Nexarch の AI チーム（/commander?view=team）の E2E。API と端末の WebSocket はモック。
 *
 * 目的（docs/PLAN-nexarch-team.md T1）: チームを作る → メンバーに役割を付ける → タスクを作る →
 * 担当へ送る → 報告を取り込む → 合格、の流れと、担当ファイルの重なりの警告・報告ありの印を確かめる。
 * 320px と 1280px で撮影し、横はみ出しが無いこと（モバイルは列が縦に並ぶ）を見る。
 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

interface Task {
  id: number; title: string; goal: string; acceptance: string; files: string[]; assignee: string;
  status: string; report: null | { summary: string; changed_files: string[]; tests: string; remaining: string };
  created_at: number; updated_at: number; history: unknown[]; warnings: string[];
  assignee_state?: string; report_ready?: boolean;
}
interface TeamState {
  teams: { id: string; name: string; created_at: number; members: { session_id: string; role: string }[]; tasks: Task[] }[];
  dispatched: number[];
  collected: number[];
}

const CONSOLES = [
  { id: "aa110001", name: "cdterm-aa110001", created_at: 1764000000, attached: false, persistent: true, engine: "v1",
    program: "claude", cwd: "~/CodeDEV/Conv", pid: 1, activity_at: 1764000500, alive: true, workload: "idle",
    commander_cli: "claude", commander_account: "default", commander_label: "開発", commander_state: "waiting",
    commander_usage: { five_hour: { used_percent: 41, resets_at: Math.floor(Date.now() / 1000) + 3600 }, seven_day: null } },
  { id: "aa110002", name: "cdterm-aa110002", created_at: 1764000000, attached: false, persistent: true, engine: "v1",
    program: "codex", cwd: "~/CodeDEV/Conv", pid: 2, activity_at: 1764000500, alive: true, workload: "idle",
    commander_cli: "codex", commander_account: "default", commander_label: "レビュー", commander_state: "working" },
];

async function mockTeams(page: Page, state: TeamState) {
  await page.route(/\/api\/v1\/commander\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1/commander/", "");
    const method = request.method();
    const body = () => request.postDataJSON() as Record<string, unknown>;
    if (path === "clis") return route.fulfill({ json: { clis: [
      { id: "claude", name: "Claude Code", executable: "claude", installed: true, multi_account: true },
      { id: "codex", name: "Codex CLI", executable: "codex", installed: true, multi_account: true }] } });
    if (path === "consoles" && method === "GET") return route.fulfill({ json: { consoles: CONSOLES } });
    if (path === "teams" && method === "GET") return route.fulfill({ json: { teams: state.teams } });
    if (path === "teams" && method === "POST") {
      const team = { id: "t0a1b2c3d", name: String(body().name), created_at: 1764000000, members: [], tasks: [] };
      state.teams.push(team);
      return route.fulfill({ status: 201, json: team });
    }
    let m = path.match(/^teams\/(\w+)\/members$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      team.members = body().members as { session_id: string; role: string }[];
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/tasks$/);
    if (m && method === "POST") {
      const team = state.teams.find((t) => t.id === m![1])!;
      const fields = body() as Partial<Task>;
      const files = fields.files ?? [];
      const warnings = team.tasks.filter((t) => t.status !== "done")
        .flatMap((t) => files.filter((f) => t.files.includes(f)).map((f) => `#${t.id} と ${f} が重なっています`));
      const task: Task = {
        id: team.tasks.length + 1, title: fields.title!, goal: fields.goal ?? "", acceptance: fields.acceptance ?? "",
        files, assignee: fields.assignee ?? "", status: "todo", report: null, created_at: 1, updated_at: 1, history: [], warnings,
        assignee_state: fields.assignee ? "waiting" : "",
      };
      team.tasks.push(task);
      return route.fulfill({ status: 201, json: task });
    }
    m = path.match(/^teams\/(\w+)\/tasks\/(\d+)(?:\/(dispatch|collect))?$/);
    if (m) {
      const team = state.teams.find((t) => t.id === m![1])!;
      const task = team.tasks.find((t) => t.id === Number(m![2]))!;
      if (m[3] === "dispatch") {
        task.status = "doing"; state.dispatched.push(task.id);
        task.report_ready = true; // 担当が報告を書いた想定
        return route.fulfill({ json: task });
      }
      if (m[3] === "collect") {
        task.status = "review"; task.report_ready = false; state.collected.push(task.id);
        task.report = { summary: "画面を作った", changed_files: ["src/a.py"], tests: "pytest 12 件合格", remaining: "なし" };
        return route.fulfill({ json: task });
      }
      if (method === "PUT") Object.assign(task, body());
      return route.fulfill({ json: task });
    }
    return route.fulfill({ status: 404, json: { detail: `unmocked: ${method} ${path}` } });
  });
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: 作る → メンバー → タスク → 送る → 取り込む → 合格`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state: TeamState = { teams: [], dispatched: [], collected: [] };
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    // チームが無い → 作る
    await page.getByRole("button", { name: "チームを作る" }).click();
    await page.getByLabel(/チームの名前/).fill("開発班");
    await page.getByRole("button", { name: "作る", exact: true }).click();
    await expect(page.locator("[data-team-name]")).toHaveText("開発班");

    // メンバーと役割（リーダーは 1 人まで）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "メンバーを選ぶ" }).click();
    const members = page.getByRole("dialog", { name: "メンバーを選ぶ" });
    await members.getByLabel("Claude Code・開発の役割").selectOption("leader");
    await expect(members.getByLabel("Codex CLI・レビューの役割").locator("option[value=leader]")).toBeDisabled();
    await members.getByLabel("Codex CLI・レビューの役割").selectOption("reviewer");
    await members.getByRole("button", { name: "保存" }).click();
    await expect(page.locator("[data-team-member]")).toHaveCount(2);
    await expect(page.locator("[data-team-member]").first()).toContainText("リーダー");
    await expect(page.locator("[data-team-member]").first()).toContainText("入力待ち");
    await expect(page.locator("[data-team-member]").first()).toContainText("5時間41%");
    await expect(page.locator("[data-team-member]").nth(1)).toContainText("作業中");

    // タスクを作る（担当ファイルが重なれば警告）
    await page.getByRole("button", { name: "＋ タスク" }).click();
    let sheet = page.getByRole("dialog", { name: "タスクを作る" });
    await sheet.getByLabel("タイトル（必須）").fill("ログイン画面");
    await sheet.getByLabel("目的").fill("ログイン画面を作る");
    await sheet.getByLabel("受け入れ条件").fill("表示される");
    await sheet.getByLabel(/担当ファイル/).fill("src/a.py\nsrc/b.py");
    await sheet.getByLabel("担当", { exact: true }).selectOption({ label: "リーダー・Claude Code・開発" });
    await sheet.getByRole("button", { name: "作る", exact: true }).click();
    await expect(page.locator('[data-team-column="todo"] [data-team-task="1"]')).toContainText("ログイン画面");

    await page.getByRole("button", { name: "＋ タスク" }).click();
    sheet = page.getByRole("dialog", { name: "タスクを作る" });
    await sheet.getByLabel("タイトル（必須）").fill("API");
    await sheet.getByLabel(/担当ファイル/).fill("src/a.py");
    await sheet.getByRole("button", { name: "作る", exact: true }).click();
    await expect(page.locator('[data-team-task="2"] [data-team-warning]')).toContainText("#1 と src/a.py が重なっています");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-board.png`, fullPage: true });

    // 担当へ送る → 作業中・報告あり
    await page.locator('[data-team-task="1"]').click();
    const detail = page.getByRole("dialog", { name: "#1 ログイン画面" });
    await expect(detail).toContainText("src/a.py, src/b.py");
    await detail.getByRole("button", { name: "担当へ送る" }).click();
    await expect(detail.locator("[data-team-task-status]")).toContainText("作業中");
    expect(state.dispatched).toEqual([1]);
    await expect(page.locator('[data-team-column="doing"] [data-team-report-ready]')).toBeVisible();

    // 報告を取り込む → レビュー待ち → 合格
    await detail.getByRole("button", { name: "報告を取り込む" }).click();
    await expect(detail.locator("[data-team-report]")).toContainText("pytest 12 件合格");
    expect(state.collected).toEqual([1]);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-detail.png`, fullPage: true });
    await detail.getByRole("button", { name: "合格" }).click();
    await page.getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(page.locator('[data-team-column="done"] [data-team-task="1"]')).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-done.png`, fullPage: true });

    // モバイルは列が縦に並ぶ（同じ x）、PC は横に並ぶ
    const a = await page.locator('[data-team-column="todo"]').boundingBox();
    const b = await page.locator('[data-team-column="doing"]').boundingBox();
    if (width < 768) expect(Math.abs(a!.x - b!.x)).toBeLessThan(2); else expect(b!.x).toBeGreaterThan(a!.x + 50);
  });
}

test("URL の ?view=team でチームが開き、コンソールの切り替えで戻れる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await establishSession(page, context);
  await mockTeams(page, { teams: [], dispatched: [], collected: [] });
  await page.goto("/commander?view=team");
  await expect(page.getByRole("button", { name: "チームを作る" })).toBeVisible();
  await expect(page.getByRole("button", { name: "チーム", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "コンソール", exact: true }).click();
  await expect(page).not.toHaveURL(/view=team/);
});
