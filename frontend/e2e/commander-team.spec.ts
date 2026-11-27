/** Nexarch の AI チーム（/commander?view=team）の E2E。API と端末の WebSocket はモック。
 *
 * 目的（docs/PLAN-nexarch-team.md T1）: チームを作る → メンバーに役割を付ける → タスクを作る →
 * 担当へ送る → 報告を取り込む → 合格、の流れと、担当ファイルの重なりの警告・報告ありの印を確かめる。
 * T2a: 未検証の印・合格の確認ダイアログ・止まった／上限の印・通知の切り替えも確かめる。
 * T2b: チームの前提（brief）の編集・送る前のリセットの切り替え・確認のコマンドの入力と結果（✓／✗・出力）・
 * 「自動で直せず」（blocked）の印も確かめる。
 * T3a: ヘッドレスのメンバーを足す・実行中の表示（最後の行・経過・使用量）と止める・上限の方針（引き継ぐ／待つ）の切り替え・
 * 候補の順番の並べ替え・同時並列の設定・板の上の「同時に動いている数／上限」・作業場所と履歴（runs）の表示も確かめる。
 * 320px と 1280px で撮影し、横はみ出しが無いこと（モバイルは列が縦に並ぶ）を見る。
 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

interface Task {
  id: number; title: string; goal: string; acceptance: string; files: string[]; assignee: string;
  status: string; report: null | { summary: string; changed_files: string[]; tests: string; remaining: string };
  created_at: number; updated_at: number; history: unknown[]; warnings: string[];
  assignee_state?: string; report_ready?: boolean; unverified?: boolean; stalled_at?: number; limited_at?: number;
  checks?: string[]; attempts?: number;
  checks_result?: { status: string; ran_at: number; results: { cmd: string; exit_code: number; seconds: number; tail: string }[]; message?: string };
  run?: Record<string, unknown>;
  runs?: Record<string, unknown>[];
  wait_until?: number;
}
interface Member {
  session_id?: string; id?: string; key?: string; kind?: string; cli?: string; account?: string; label?: string; role: string;
}
interface TeamState {
  teams: {
    id: string; name: string; created_at: number; notify?: boolean; brief?: string; reset_before_dispatch?: boolean;
    on_limit?: string; fallback?: string[]; max_parallel?: number; local_parallel?: number; auto_run?: boolean;
    workdir?: string; workdir_folder?: string; workdir_name?: string; parallel?: Record<string, number>;
    members: Member[]; tasks: Task[];
  }[];
  stopped?: number[];
  dispatched: number[];
  collected: number[];
  notifyCalls?: boolean[];
  teamPuts?: Record<string, unknown>[];
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
      { id: "codex", name: "Codex CLI", executable: "codex", installed: true, multi_account: true },
      { id: "opencode", name: "OpenCode", executable: "opencode2", installed: true, multi_account: true }] } });
    if (path.startsWith("accounts/")) return route.fulfill({ json: { accounts: [
      { name: "default", default: true, logged_in: null, label: "", title: "既定の接続" }] } });
    if (path === "consoles" && method === "GET") return route.fulfill({ json: { consoles: CONSOLES } });
    if (path === "teams" && method === "GET") return route.fulfill({ json: { teams: state.teams } });
    if (path === "teams" && method === "POST") {
      const team = { id: "t0a1b2c3d", name: String(body().name), created_at: 1764000000, members: [], tasks: [] };
      state.teams.push(team);
      return route.fulfill({ status: 201, json: team });
    }
    let m = path.match(/^teams\/(\w+)\/notify$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      team.notify = body().notify as boolean;
      (state.notifyCalls ??= []).push(team.notify);
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      const fields = body();
      (state.teamPuts ??= []).push(fields);
      if (typeof fields.brief === "string") team.brief = fields.brief;
      if (typeof fields.reset_before_dispatch === "boolean") team.reset_before_dispatch = fields.reset_before_dispatch;
      if (typeof fields.on_limit === "string") team.on_limit = fields.on_limit;
      if (Array.isArray(fields.fallback)) team.fallback = fields.fallback as string[];
      if (typeof fields.max_parallel === "number") team.max_parallel = fields.max_parallel;
      if (typeof fields.local_parallel === "number") team.local_parallel = fields.local_parallel;
      if (typeof fields.auto_run === "boolean") team.auto_run = fields.auto_run;
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/members$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      let n = 0;
      team.members = (body().members as Member[]).map((x) => {
        const id = x.kind === "headless" ? x.id ?? `h0000e2e${n++}` : undefined;
        return { ...x, id, key: id ?? x.session_id, kind: x.kind ?? "console" };
      });
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
        files, checks: fields.checks ?? [], assignee: fields.assignee ?? "", status: "todo", report: null, created_at: 1, updated_at: 1, history: [], warnings,
        assignee_state: fields.assignee ? "waiting" : "",
      };
      team.tasks.push(task);
      return route.fulfill({ status: 201, json: task });
    }
    m = path.match(/^teams\/(\w+)\/tasks\/(\d+)(?:\/(dispatch|collect|stop))?$/);
    if (m) {
      const team = state.teams.find((t) => t.id === m![1])!;
      const task = team.tasks.find((t) => t.id === Number(m![2]))!;
      if (m[3] === "dispatch") {
        task.status = "doing"; state.dispatched.push(task.id);
        task.report_ready = true; // 担当が報告を書いた想定
        return route.fulfill({ json: task });
      }
      if (m[3] === "stop") {
        (state.stopped ??= []).push(task.id);
        return route.fulfill({ json: { ok: true } });
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

function seededTeam(tasks: Partial<Task>[]): TeamState {
  const full = tasks.map((t, i): Task => ({
    id: i + 1, title: `タスク${i + 1}`, goal: "", acceptance: "", files: [], assignee: "aa110001", status: "todo", report: null,
    created_at: 1, updated_at: 1, history: [], warnings: [], assignee_state: "waiting", ...t,
  }));
  return {
    teams: [{ id: "t0a1b2c3d", name: "開発班", created_at: 1, notify: true, members: [{ session_id: "aa110001", role: "developer" }], tasks: full }],
    dispatched: [], collected: [],
  };
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: 未検証の印と合格の確認・止まった／上限の印・通知の切り替え`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = seededTeam([
      { status: "review", title: "未検証の報告", unverified: true,
        report: { summary: "作った", changed_files: [], tests: "なし", verify: "", remaining: "なし" } },
      { status: "review", title: "検証済みの報告", unverified: false,
        report: { summary: "作った", changed_files: [], tests: "pytest 12 件合格", verify: "pytest -q → 12 passed", remaining: "なし" } },
      { status: "doing", title: "止まった作業", stalled_at: 1764000000 },
      { status: "doing", title: "上限の作業", limited_at: 1764000000 },
    ]);
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    await expect(page.locator('[data-team-task="1"] [data-team-unverified]')).toContainText("未検証");
    await expect(page.locator('[data-team-task="2"] [data-team-unverified]')).toHaveCount(0);
    await expect(page.locator('[data-team-task="3"] [data-team-stalled]')).toContainText("止まった（報告なし）");
    await expect(page.locator('[data-team-task="4"] [data-team-limited]')).toContainText("上限で停止");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2a-board.png`, fullPage: true });

    // 検証済みは確認なしで合格
    await page.locator('[data-team-task="2"]').click();
    let detail = page.getByRole("dialog", { name: "#2 検証済みの報告" });
    await expect(detail).toContainText("確認: pytest -q → 12 passed");
    await detail.getByRole("button", { name: "合格" }).click();
    await expect(page.getByText("確認の結果がありません。合格にしますか？")).toHaveCount(0);
    await expect.poll(() => state.teams[0].tasks[1].status).toBe("done");
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // 未検証は確認ダイアログ。やめれば合格にならない・進めれば合格
    await page.locator('[data-team-task="1"]').click();
    detail = page.getByRole("dialog", { name: "#1 未検証の報告" });
    await expect(detail.locator("[data-team-unverified]")).toBeVisible();
    await detail.getByRole("button", { name: "合格" }).click();
    await expect(page.getByText("確認の結果がありません。合格にしますか？")).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2a-confirm.png`, fullPage: true });
    await page.getByRole("button", { name: "キャンセル" }).click();
    expect(state.teams[0].tasks[0].status).toBe("review");
    await detail.getByRole("button", { name: "合格" }).click();
    await page.getByRole("button", { name: "合格にする" }).click();
    await expect.poll(() => state.teams[0].tasks[0].status).toBe("done");
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // 通知の切り替え
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: /通知: オン/ }).click();
    await expect.poll(() => state.notifyCalls).toEqual([false]);
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /通知: オフ/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await noOverflow(page);
  });
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: チームの前提・リセットの切り替え・確認のコマンドと結果・自動で直せず`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = seededTeam([
      { status: "review", title: "確認が通った", checks: ["pytest -q", "ruff check ."], unverified: false,
        report: { summary: "作った", changed_files: [], tests: "ok", remaining: "なし" },
        checks_result: { status: "passed", ran_at: 1764000000, results: [
          { cmd: "pytest -q", exit_code: 0, seconds: 3.2, tail: "12 passed" },
          { cmd: "ruff check .", exit_code: 0, seconds: 0.4, tail: "All checks passed!" }] } },
      { status: "doing", title: "確認が落ちて再挑戦", checks: ["pytest -q"], attempts: 1,
        checks_result: { status: "failed", ran_at: 1764000000, results: [
          { cmd: "pytest -q", exit_code: 1, seconds: 2.5, tail: "FAILED tests/test_a.py::test_x - assert 1 == 2" }] } },
      { status: "blocked", title: "自動で直せなかった", checks: ["pytest -q"], attempts: 2,
        checks_result: { status: "failed", ran_at: 1764000000, results: [
          { cmd: "pytest -q", exit_code: 1, seconds: 2.5, tail: "FAILED tests/test_a.py::test_x" }] } },
      { status: "review", title: "確認の実行中", checks: ["pytest -q"], unverified: false,
        report: { summary: "作った", changed_files: [], tests: "ok", remaining: "なし" },
        checks_result: { status: "running", ran_at: 1764000000, results: [] } },
    ]);
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    // 印: 合格・不合格・実行中・自動で直せず（blocked は「待ち」の列に赤い印）
    await expect(page.locator('[data-team-task="1"] [data-team-checks="passed"]')).toContainText("確認: 合格");
    await expect(page.locator('[data-team-task="2"] [data-team-checks="failed"]')).toContainText("確認: 不合格");
    await expect(page.locator('[data-team-task="4"] [data-team-checks="running"]')).toContainText("確認: 実行中");
    await expect(page.locator('[data-team-column="todo"] [data-team-task="3"] [data-team-blocked]')).toContainText("自動で直せず");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2b-board.png`, fullPage: true });

    // 詳細: コマンドごとの ✓／✗・終了コード・出力の最後（折りたたみ）
    await page.locator('[data-team-task="1"]').click();
    let detail = page.getByRole("dialog", { name: "#1 確認が通った" });
    await expect(detail.locator("[data-team-check-row]")).toHaveCount(2);
    await expect(detail.locator("[data-team-check-row]").first()).toContainText("✓");
    await expect(detail.locator("[data-team-check-row]").first()).toContainText("終了 0");
    await detail.locator("summary").first().click();
    await expect(detail.getByText("12 passed")).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2b-checks.png`, fullPage: true });
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    await page.locator('[data-team-task="3"]').click();
    detail = page.getByRole("dialog", { name: "#3 自動で直せなかった" });
    await expect(detail.locator("[data-team-task-status]")).toContainText("確認が通らず、自動で直せませんでした");
    await expect(detail.locator("[data-team-check-row]").first()).toContainText("✗");
    await expect(detail.locator("[data-team-check-row]").first()).toContainText("終了 1");
    await expect(detail).toContainText("自動で直させた回数 2/2");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2b-blocked.png`, fullPage: true });

    // 確認のコマンドの編集（詳細）
    await detail.getByRole("button", { name: "編集" }).click();
    await detail.getByLabel("確認のコマンド（1 行に 1 つ）").fill("pytest -q\nruff check .");
    await detail.getByRole("button", { name: "確認のコマンドを保存" }).click();
    await expect.poll(() => state.teams[0].tasks[2].checks).toEqual(["pytest -q", "ruff check ."]);
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // チームの前提（シート）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "チームの前提" }).click();
    const brief = page.getByRole("dialog", { name: "チームの前提" });
    await brief.getByLabel("前提").fill("pytest で試験する。\n本番には触らない。");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t2b-brief.png`, fullPage: true });
    await brief.getByRole("button", { name: "保存" }).click();
    await expect.poll(() => state.teams[0].brief).toBe("pytest で試験する。\n本番には触らない。");

    // 送る前のリセットの切り替え（既定オン → オフ → オン）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: /会話をリセット: オン/ }).click();
    await expect.poll(() => state.teams[0].reset_before_dispatch).toBe(false);
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /会話をリセット: オフ/ })).toBeVisible();
    await page.getByRole("menuitem", { name: /会話をリセット: オフ/ }).click();
    await expect.poll(() => state.teams[0].reset_before_dispatch).toBe(true);

    // タスクの作成で確認のコマンドを入力
    await page.getByRole("button", { name: "＋ タスク" }).click();
    const sheet = page.getByRole("dialog", { name: "タスクを作る" });
    await sheet.getByLabel("タイトル（必須）").fill("新しいタスク");
    await sheet.getByLabel("確認のコマンド（1 行に 1 つ）").fill("pytest -q\nnpm run build");
    await sheet.getByRole("button", { name: "作る", exact: true }).click();
    await expect.poll(() => state.teams[0].tasks[4]?.checks).toEqual(["pytest -q", "npm run build"]);
    await noOverflow(page);
  });
}

function headlessTeam(): TeamState {
  const task = (id: number, fields: Partial<Task>): Task => ({
    id, title: `タスク${id}`, goal: "", acceptance: "", files: [], assignee: "", status: "todo", report: null,
    created_at: 1, updated_at: 1, history: [], warnings: [], assignee_state: "", ...fields,
  });
  const now = Math.floor(Date.now() / 1000);
  return {
    teams: [{
      id: "t0a1b2c3d", name: "開発班", created_at: 1, notify: true, on_limit: "handoff", fallback: [], max_parallel: 3, local_parallel: 1, auto_run: true,
      workdir: "/data1tb/CodeDEV/team-20261127-120000", workdir_folder: "team-20261127-120000", workdir_name: "開発班",
      parallel: { running: 2, max: 3, waiting: 1, local_max: 1 },
      members: [
        { session_id: "aa110001", key: "aa110001", kind: "console", role: "reviewer" },
        { id: "h0000aaaa", key: "h0000aaaa", kind: "headless", cli: "claude", account: "default", role: "developer", label: "自動係" },
        { id: "h0000bbbb", key: "h0000bbbb", kind: "headless", cli: "opencode", account: "default", role: "member", label: "ローカル" },
      ],
      tasks: [
        task(1, {
          title: "ログイン画面", status: "doing", assignee: "h0000aaaa", assignee_state: "working",
          run: { run_id: "r1", status: "running", member: "h0000aaaa", cli: "claude", started_at: now - 75, elapsed: 75,
            last_line: "Bash: pytest -q", progress: ["Edit: src/a.py", "Bash: pytest -q"], usage: { input_tokens: 1200, output_tokens: 340, cost_usd: 0.25 },
            tail: "", exit_code: null },
          runs: [{ n: 1, kind: "headless", member: "h0000aaaa", cli: "claude", account: "default", label: "自動係", started_at: now - 75, ended_at: null,
            seconds: null, result: "running", usage: null }],
        }),
        task(2, { title: "API の追加", status: "todo", assignee: "h0000bbbb", assignee_state: "waiting" }),
        task(3, {
          title: "上限で引き継ぎ", status: "doing", assignee: "h0000aaaa", assignee_state: "limited", limited_at: now - 60,
          wait_until: now + 1800,
          run: { run_id: "r3", status: "limited", member: "h0000aaaa", cli: "claude", started_at: now - 300, ended_at: now - 60,
            last_line: "Edit: src/b.py", progress: [], usage: { input_tokens: 5000, output_tokens: 900, cost_usd: 0.8 }, tail: "5-hour limit reached", exit_code: 1 },
          runs: [
            { n: 1, kind: "headless", member: "h0000bbbb", cli: "opencode", account: "default", label: "ローカル", started_at: now - 900, ended_at: now - 700,
              seconds: 200, result: "limited", usage: { input_tokens: 900, output_tokens: 100, cost_usd: 0 } },
            { n: 2, kind: "headless", member: "h0000aaaa", cli: "claude", account: "default", label: "自動係", started_at: now - 300, ended_at: now - 60,
              seconds: 240, result: "limited", usage: { input_tokens: 5000, output_tokens: 900, cost_usd: 0.8 } },
          ],
        }),
      ],
    }],
    dispatched: [], collected: [], stopped: [],
  };
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: ヘッドレス（実行中の表示・止める）・上限の方針・候補の順番・同時並列`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = headlessTeam();
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    // ヘッドレスのメンバー・同時に動いている数・作業場所
    await expect(page.locator('[data-team-member][data-team-member-kind="headless"]')).toHaveCount(2);
    await expect(page.locator('[data-team-member-kind="headless"]').first()).toContainText("ヘッドレス");
    await expect(page.locator('[data-team-member-kind="headless"]').first()).toContainText("実行中");
    await expect(page.locator("[data-team-parallel]")).toContainText("2／3");
    await expect(page.locator("[data-team-parallel]")).toContainText("待ち 1");
    await expect(page.locator("[data-team-workdir]")).toContainText("開発班");
    await expect(page.getByRole("link", { name: "Project Lab で開く" })).toHaveAttribute("href", /project=team-20261127-120000/);

    // カード: 実行中（ヘッドレス）・最後の行・使用量／回復を待つ
    await expect(page.locator('[data-team-task="1"] [data-team-running]')).toContainText("実行中（ヘッドレス）");
    await expect(page.locator('[data-team-task="1"] [data-team-run-line]')).toContainText("Bash: pytest -q");
    await expect(page.locator('[data-team-task="1"] [data-team-run-line]')).toContainText("1,540 トークン");
    await expect(page.locator('[data-team-task="3"] [data-team-waiting]')).toContainText("回復を待つ");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-board.png`, fullPage: true });

    // 詳細: 実行の状態・経過・使用量・止める
    await page.locator('[data-team-task="1"]').click();
    let detail = page.getByRole("dialog", { name: "#1 ログイン画面" });
    await expect(detail.locator("[data-team-run-status]")).toHaveAttribute("data-team-run-status", "running");
    await expect(detail.locator("[data-team-run-usage]")).toContainText("$0.25");
    await expect(detail.getByRole("button", { name: "実行する" })).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-running.png`, fullPage: true });
    await detail.getByRole("button", { name: "止める" }).click();
    await expect.poll(() => state.stopped).toEqual([1]);
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // 詳細: 担い手の履歴（runs）
    await page.locator('[data-team-task="3"]').click();
    detail = page.getByRole("dialog", { name: "#3 上限で引き継ぎ" });
    await expect(detail.locator("[data-team-runs] li")).toHaveCount(2);
    await expect(detail.locator("[data-team-runs] li").first()).toContainText("ローカル");
    await expect(detail.locator("[data-team-runs] li").first()).toContainText("上限で停止");
    await expect(detail.locator("[data-team-run-panel]")).toContainText("回復を待っています");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-runs.png`, fullPage: true });
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // 上限の方針の切り替え（既定: 引き継ぐ → 回復を待つ → 引き継ぐ）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: /上限で止まったら: 別の担い手へ引き継ぐ/ }).click();
    await expect.poll(() => state.teams[0].on_limit).toBe("wait");
    await expect(page.locator("[data-team-parallel]")).toContainText("上限: 回復を待つ");
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /上限で止まったら: 回復を待つ/ })).toBeVisible();
    await page.getByRole("menuitem", { name: /上限で止まったら: 回復を待つ/ }).click();
    await expect.poll(() => state.teams[0].on_limit).toBe("handoff");

    // 候補の順番（並べ替えて保存）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "候補の順番" }).click();
    const order = page.getByRole("dialog", { name: "候補の順番" });
    await expect(order.locator("[data-team-fallback-row]")).toHaveCount(3);
    await order.getByRole("button", { name: /OpenCode・ローカルを上へ/ }).click();
    await order.getByRole("button", { name: /OpenCode・ローカルを上へ/ }).click();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-order.png`, fullPage: true });
    await order.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => state.teams[0].fallback).toEqual(["h0000bbbb", "aa110001", "h0000aaaa"]);

    // 同時並列の設定
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "同時並列の設定" }).click();
    const par = page.getByRole("dialog", { name: "同時並列の設定" });
    await par.getByLabel(/同時に走らせるタスクの数/).fill("5");
    await par.getByLabel(/ローカルの担い手の同時の数/).fill("2");
    await par.getByLabel(/自動で割り当てる/).uncheck();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-parallel.png`, fullPage: true });
    await par.getByRole("button", { name: "保存" }).click();
    await expect.poll(() => [state.teams[0].max_parallel, state.teams[0].local_parallel, state.teams[0].auto_run]).toEqual([5, 2, false]);
    await expect(page.locator("[data-team-parallel]")).toContainText("自動の割り当て: オフ");
  });

  test(`AI チーム ${label}px: ヘッドレスのメンバーを足す`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = seededTeam([]);
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "メンバーを選ぶ" }).click();
    const sheet = page.getByRole("dialog", { name: "メンバーを選ぶ" });
    await sheet.getByLabel("CLI", { exact: true }).selectOption("codex");
    await sheet.getByLabel("役割", { exact: true }).selectOption("reviewer");
    await sheet.getByLabel("名前（任意）").fill("夜間の係");
    await sheet.getByRole("button", { name: "ヘッドレスのメンバーを足す" }).click();
    await expect(sheet.locator("[data-team-headless-row]")).toHaveCount(1);
    await expect(sheet.locator("[data-team-headless-row]")).toContainText("夜間の係");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-members.png`, fullPage: true });
    await sheet.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.locator('[data-team-member-kind="headless"]')).toContainText("夜間の係");
    await expect(page.locator('[data-team-member-kind="headless"]')).toContainText("待機中（画面なし）");
    expect(state.teams[0].members.find((m) => m.kind === "headless")).toMatchObject({ cli: "codex", account: "default", role: "reviewer", label: "夜間の係" });
    // 担当の選択肢にも出る（ヘッドレスと分かる）
    await page.getByRole("button", { name: "＋ タスク" }).click();
    await expect(page.getByRole("dialog", { name: "タスクを作る" }).getByLabel("担当", { exact: true }).locator("option", { hasText: "（ヘッドレス）" })).toHaveCount(1);
  });
}
