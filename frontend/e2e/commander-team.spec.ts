/** Nexarch の AI チーム（/commander?view=team）の E2E。API と端末の WebSocket はモック。
 *
 * 目的（docs/PLAN-nexarch-team.md T1）: チームを作る → メンバーに役割を付ける → タスクを作る →
 * 担当へ送る → 報告を取り込む → 合格、の流れと、担当ファイルの重なりの警告・報告ありの印を確かめる。
 * T2a: 未検証の印・合格の確認ダイアログ・止まった／上限の印・通知の切り替えも確かめる。
 * T2b: チームの前提（brief）の編集・送る前のリセットの切り替え・確認のコマンドの入力と結果（✓／✗・出力）・
 * 「自動で直せず」（blocked）の印も確かめる。
 * T3a: ヘッドレスのメンバーを足す・実行中の表示（最後の行・経過・使用量）と止める・上限の方針（引き継ぐ／待つ）の切り替え・
 * 候補の順番の並べ替え・同時並列の設定・板の上の「同時に動いている数／上限」・作業場所と履歴（runs）の表示も確かめる。
 * T3b: 目標からチームを作る（チームリーダーの応答はモック）・役割の欄（自動の振り分け）・担い手の選び手（コンソールとモデルが別・
 * 考えるレベル）・引き継ぎ先を選ぶ（タスクのカードとコンソールのカード）・チームの既定（チームリーダー・保存先を一度だけ）。
 * 利用者の決まり（2026-11-27）「不要な表示は隠し、必要ならエキスパートで開く」: 切（既定）と入の両方を撮る。
 * 既存の T1〜T3a の流れはエキスパートを入にして通す（beforeEach）。タイトルに「エキスパート切」を含む試験だけ切のまま。
 * 320px と 1280px で撮影し、横はみ出しが無いこと（モバイルは列が縦に並ぶ）を見る。
 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 確認のコマンド。文字列か、作業フォルダを指定したもの。 */
type Check = string | { command: string; cwd?: string };
interface Task {
  role?: string; blocked_reason?: string;
  id: number; title: string; goal: string; acceptance: string; files: string[]; assignee: string;
  status: string; report: null | { summary: string; changed_files: string[]; tests: string; remaining: string };
  created_at: number; updated_at: number; history: unknown[]; warnings: string[];
  assignee_state?: string; report_ready?: boolean; unverified?: boolean; stalled_at?: number; limited_at?: number;
  checks?: Check[]; attempts?: number;
  checks_result?: { status: string; ran_at: number; results: { cmd: string; exit_code: number; seconds: number; tail: string }[]; message?: string };
  run?: Record<string, unknown>;
  runs?: Record<string, unknown>[];
  wait_until?: number;
  leader_managed?: boolean; tier?: string; item_id?: string; branch?: string;
}
interface Member {
  session_id?: string; id?: string; key?: string; kind?: string; cli?: string; account?: string; label?: string; role: string;
  no_sandbox?: boolean;
}
interface TeamState {
  teams: {
    id: string; name: string; created_at: number; notify?: boolean; brief?: string; reset_before_dispatch?: boolean;
    on_limit?: string; codex_without_sandbox?: boolean; fallback?: string[]; max_parallel?: number; local_parallel?: number; auto_run?: boolean;
    local_only?: boolean; auto_resume?: boolean; inputs?: { path: string; source?: string; label?: string }[];
    workdir?: string; workdir_folder?: string; workdir_name?: string; parallel?: Record<string, number>;
    roles?: Record<string, unknown>[]; leader?: unknown; leader_effective?: unknown; leader_run?: Record<string, unknown>;
    plan?: Record<string, unknown>; timeline?: Record<string, unknown>[]; requests?: Record<string, unknown>[]; mcp_log?: Record<string, unknown>[];
    questions?: { id: string; question: string; options: string[]; asked_at: number; answer: string | null; answered_at: number | null }[];
    result?: { summary: string; outcome: string; finished_at: number; remaining: string[]; checks: Record<string, unknown> | null;
      changes: Record<string, unknown> | null; usage: Record<string, number>; can_land: boolean; landed: boolean; team_branch: string; base_branch: string };
    usage_total?: Record<string, number>; git?: Record<string, unknown>; autonomy?: string;
    members: Member[]; tasks: Task[];
  }[];
  settings?: { resume_message: string; default_resume_message: string; default_leader: unknown; default_roles: unknown[]; team_root: string };
  modelsReqs?: string[];
  choiceReqs?: number;
  autoPosts?: Record<string, unknown>[];
  askPosts?: Record<string, unknown>[];
  answers?: Record<string, unknown>[];
  leaderCalls?: string[];
  rolesPuts?: Record<string, unknown>[][];
  handoffPosts?: Record<string, unknown>[];
  defaultsPuts?: Record<string, unknown>[];
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

/** 役割の例: 実装（プライマリが回復待ちでセカンダリで動作中）・レビュー・試験（担い手が未設定）。 */
const RECOVER_AT = 1_900_000_000;
const AUTO_ROLES = [
  { id: "r1", name: "実装", count: 2, note: "コードを書く", running: 1, active_index: 1, on_secondary: true, primary_recovers_at: RECOVER_AT,
    primary: { kind: "headless", cli: "claude", account: "default", model: "opus", effort: "high" },
    secondary: [{ kind: "headless", cli: "codex", account: "default", model: "gpt-a", effort: "low" }], last_index: 1 },
  { id: "r2", name: "レビュー", count: 1, note: "", running: 0, active_index: 0, on_secondary: false, primary_recovers_at: null,
    primary: { kind: "headless", cli: "claude", account: "default", model: "", effort: "" }, secondary: [] },
  { id: "r3", name: "試験", count: 1, note: "", running: 0, active_index: null, on_secondary: false, primary_recovers_at: null, primary: null, secondary: [] },
];

/** サーバーが返す「使えるモデル」の一覧（まとまりごと）。選ぶと CLI・接続・モデル・考えるレベルが決まる。 */
const MODEL_CHOICES = [
  { id: "local:glm", label: "ローカルの GLM", group: "local",
    endpoint: { kind: "headless", cli: "opencode", account: "gateway", model: "glm", effort: "" }, efforts: [] },
  { id: "opencode:free", label: "OpenCode（無料）", group: "opencode",
    endpoint: { kind: "headless", cli: "opencode", account: "free", model: "big", effort: "" }, efforts: [] },
  { id: "openrouter:deepseek", label: "DeepSeek（OpenRouter）", group: "openrouter",
    endpoint: { kind: "headless", cli: "opencode", account: "openrouter", model: "deepseek/v4", effort: "" }, efforts: [] },
  { id: "claude:opus", label: "Claude Opus", group: "claude",
    endpoint: { kind: "headless", cli: "claude", account: "default", model: "opus", effort: "high" }, efforts: ["low", "medium", "high"] },
  { id: "codex:gpt", label: "GPT A（Codex）", group: "codex",
    endpoint: { kind: "headless", cli: "codex", account: "default", model: "gpt-a", effort: "" }, efforts: [] },
];

async function mockTeams(page: Page, state: TeamState) {
  await page.route(/\/api\/v1\/commander\//, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1/commander/", "");
    const method = request.method();
    const body = () => request.postDataJSON() as Record<string, unknown>;
    let m: RegExpMatchArray | null;
    if (path === "clis") return route.fulfill({ json: { clis: [
      { id: "claude", name: "Claude Code", executable: "claude", installed: true, multi_account: true },
      { id: "codex", name: "Codex CLI", executable: "codex", installed: true, multi_account: true },
      { id: "opencode", name: "OpenCode", executable: "opencode2", installed: true, multi_account: true }] } });
    if (path.startsWith("accounts/")) return route.fulfill({ json: { accounts: [
      { name: "default", default: true, logged_in: null, label: "", title: "既定の接続" }] } });
    if (path === "consoles" && method === "GET") return route.fulfill({ json: { consoles: CONSOLES } });
    if (path === "teams" && method === "GET") return route.fulfill({ json: { teams: state.teams } });
    if (path.startsWith("models") && method === "GET") {
      const q = new URL(request.url()).searchParams;
      (state.modelsReqs ??= []).push(`${q.get("cli")}/${q.get("account")}`);
      const claude = q.get("cli") === "claude";
      return route.fulfill({ json: {
        cli: q.get("cli"), account: q.get("account"), free_input: true, default_model: "", source: "mock", note: claude ? "別名のほか、完全なモデルの名前も入力できます" : "",
        efforts: claude ? ["low", "medium", "high", "xhigh", "max"] : ["low", "high"],
        models: claude
          ? ["fable", "opus", "sonnet", "haiku"].map((id) => ({ id, label: id, efforts: ["low", "medium", "high", "xhigh", "max"], default_effort: "" }))
          : [{ id: "gpt-a", label: "GPT A", efforts: ["low", "high"], default_effort: "low" }],
      } });
    }
    if (path === "model-choices" && method === "GET") {
      state.choiceReqs = (state.choiceReqs ?? 0) + 1;
      return route.fulfill({ json: { note: "モックのモデル一覧", choices: MODEL_CHOICES } });
    }
    if (path === "settings" && method === "GET") return route.fulfill({ json: state.settings ?? { resume_message: "続けてください", default_resume_message: "続けてください", default_leader: null, default_roles: [], team_root: "" } });
    if (path === "settings/team-defaults" && method === "PUT") {
      const fields = body();
      (state.defaultsPuts ??= []).push(fields);
      state.settings = { resume_message: "続けてください", default_resume_message: "続けてください", default_leader: null, default_roles: [], team_root: "", ...state.settings };
      if ("team_root" in fields) state.settings.team_root = String(fields.team_root);
      if ("default_leader" in fields) state.settings.default_leader = fields.default_leader;
      if ("default_roles" in fields) state.settings.default_roles = fields.default_roles as unknown[];
      return route.fulfill({ json: state.settings });
    }
    if (path === "teams/auto" && method === "POST") {
      (state.autoPosts ??= []).push(body());
      const team = {
        id: "t0auto001", name: String(body().goal).slice(0, 20), created_at: 1764000000, notify: true, members: [], tasks: [], roles: AUTO_ROLES,
        inputs: Array.isArray(body().input_paths) ? (body().input_paths as string[]).map((p) => ({ path: p, source: "workdir" })) : [],
        leader_effective: { kind: "headless", cli: "claude", account: "default", model: "", effort: "" },
        leader_run: { status: "running", endpoint: "Claude Code", started_at: 1764000000 },
      };
      state.teams.push(team);
      return route.fulfill({ status: 201, json: team });
    }
    m = path.match(/^teams\/(\w+)\/leader\/run$/);
    if (m && method === "POST") {
      (state.askPosts ??= []).push(body());
      const team = state.teams.find((t) => t.id === m![1])!;
      if (Array.isArray(body().input_paths)) {
        team.inputs = (body().input_paths as string[]).map((p) => ({ path: p, source: "workdir" }));
      }
      team.leader_run = { status: "running", endpoint: "Claude Code" };
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/questions\/(q\d+)\/answer$/);
    if (m && method === "POST") {
      (state.answers ??= []).push({ id: m[2], ...body() });
      const team = state.teams.find((t) => t.id === m![1])!;
      const q = (team.questions ?? []).find((x) => x.id === m![2])!;
      q.answer = String(body().answer);
      return route.fulfill({ json: { ok: true } });
    }
    m = path.match(/^teams\/(\w+)\/leader\/(land|resume|stop)$/);
    if (m && method === "POST") {
      (state.leaderCalls ??= []).push(m[2]);
      const team = state.teams.find((t) => t.id === m![1])!;
      if (m[2] === "land" && team.result) { team.result.landed = true; team.result.can_land = false; }
      if (m[2] === "resume") team.leader_run = { status: "running", endpoint: "Claude Code" };
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/roles$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      (state.rolesPuts ??= []).push(body().roles as Record<string, unknown>[]);
      team.roles = body().roles as Record<string, unknown>[];
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/leader$/);
    if (m && method === "PUT") {
      const team = state.teams.find((t) => t.id === m![1])!;
      team.leader = body().leader;
      return route.fulfill({ json: team });
    }
    m = path.match(/^teams\/(\w+)\/tasks\/(\d+)\/handoff$/);
    if (m && method === "POST") {
      const team = state.teams.find((t) => t.id === m![1])!;
      const task = team.tasks.find((t) => t.id === Number(m![2]))!;
      (state.handoffPosts ??= []).push(body());
      task.status = "doing"; task.blocked_reason = undefined;
      return route.fulfill({ json: task });
    }
    if (path === "teams" && method === "POST") {
      const team = { id: "t0a1b2c3d", name: String(body().name), created_at: 1764000000, members: [], tasks: [] };
      state.teams.push(team);
      return route.fulfill({ status: 201, json: team });
    }
    m = path.match(/^teams\/(\w+)\/notify$/);
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
      if (typeof fields.codex_without_sandbox === "boolean") team.codex_without_sandbox = fields.codex_without_sandbox;
      if (typeof fields.local_only === "boolean") team.local_only = fields.local_only;
      if (typeof fields.auto_resume === "boolean") team.auto_resume = fields.auto_resume;
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

// 既存の T1〜T3a の流れはエキスパートを入にして通す（切のままにするのはタイトルに「エキスパート切」を含む試験だけ）
test.beforeEach(async ({ page }, info) => {
  if (info.title.includes("エキスパート切")) return;
  await page.addInitScript(() => { try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ } });
});

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: 作る → メンバー → タスク → 送る → 取り込む → 合格`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state: TeamState = { teams: [], dispatched: [], collected: [] };
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    // チームが無い → 作る
    await page.getByRole("button", { name: "チームを作る", exact: true }).click();
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
  await expect(page.getByRole("button", { name: "チームを作る", exact: true })).toBeVisible();
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
      { status: "review", title: "確認が通った", checks: ["pytest -q", { command: "npm test", cwd: "web" }], unverified: false,
        report: { summary: "作った", changed_files: [], tests: "ok", remaining: "なし" },
        checks_result: { status: "passed", ran_at: 1764000000, results: [
          { cmd: "pytest -q", exit_code: 0, seconds: 3.2, tail: "12 passed" },
          { cmd: "npm test", exit_code: 0, seconds: 0.4, tail: "All checks passed!" }] } },
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
    // 作業フォルダ付きの確認は、その場所を添えて見せる
    await expect(detail.locator("[data-team-check-row]").nth(1)).toContainText("npm test（web）");
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

    // 変えずに保存しても、作業フォルダ付きの指定はそのまま残る
    await page.locator('[data-team-task="1"]').click();
    detail = page.getByRole("dialog", { name: "#1 確認が通った" });
    await detail.getByRole("button", { name: "編集" }).click();
    await detail.getByRole("button", { name: "確認のコマンドを保存" }).click();
    await expect.poll(() => state.teams[0].tasks[0].checks).toEqual(["pytest -q", { command: "npm test", cwd: "web" }]);
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
        { id: "h0000cccc", key: "h0000cccc", kind: "headless", cli: "codex", account: "default", role: "member", label: "夜間", no_sandbox: true },
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
            last_line: "Edit: src/b.py", progress: [], usage: { input_tokens: 5000, output_tokens: 900, cost_usd: 0.8 }, tail: "5-hour limit reached", exit_code: 1,
          sandbox: "none", failed_ops: 2, failed_note: "失敗した操作 2 件" },
          runs: [
            { n: 1, kind: "headless", member: "h0000bbbb", cli: "opencode", account: "default", label: "ローカル", started_at: now - 900, ended_at: now - 700,
              seconds: 200, result: "limited", usage: { input_tokens: 900, output_tokens: 100, cost_usd: 0 } },
            { n: 2, kind: "headless", member: "h0000aaaa", cli: "claude", account: "default", label: "自動係", started_at: now - 300, ended_at: now - 60,
              seconds: 240, result: "limited", usage: { input_tokens: 5000, output_tokens: 900, cost_usd: 0.8 } },
            { n: 3, kind: "headless", member: "h0000cccc", cli: "codex", account: "default", label: "夜間", started_at: now - 50, ended_at: now - 20,
              seconds: 30, result: "write_failed", usage: null, sandbox: "none" },
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
    await expect(page.locator('[data-team-member][data-team-member-kind="headless"]')).toHaveCount(3);
    await expect(page.locator("[data-team-no-sandbox]")).toHaveCount(1);
    await expect(page.locator("[data-team-no-sandbox]")).toContainText("サンドボックスなし");
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
    await expect(detail.locator("[data-team-runs] li")).toHaveCount(3);
    await expect(detail.locator("[data-team-runs] li").nth(2)).toContainText("サンドボックスなし（この機械では使えない）");
    await expect(detail.locator("[data-team-run-failed]")).toContainText("失敗した操作 2 件");
    await expect(detail.locator("[data-team-run-sandbox]")).toBeVisible();
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
    await expect(order.locator("[data-team-fallback-row]")).toHaveCount(4);
    await order.getByRole("button", { name: /OpenCode・ローカルを上へ/ }).click();
    await order.getByRole("button", { name: /OpenCode・ローカルを上へ/ }).click();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-t3a-order.png`, fullPage: true });
    await order.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => state.teams[0].fallback).toEqual(["h0000bbbb", "aa110001", "h0000aaaa", "h0000cccc"]);

    // Codex のサンドボックスが使えないときの方針（既定: サンドボックスなしで動かす → 止める）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: /Codex のサンドボックスが使えないとき: サンドボックスなしで動かす/ }).click();
    await expect.poll(() => state.teams[0].codex_without_sandbox).toBe(false);
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /Codex のサンドボックスが使えないとき: 止める/ })).toBeVisible();
    await page.keyboard.press("Escape");

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


// ---------------------------------------------------------------- T3b

const ROOT = "/data1tb";
const EXPERT_SWITCH = { name: "エキスパート" } as const;

/** FilePicker（Nexarch の作業フォルダ API）と端末の WebSocket のモック。 */
async function mockPickerAndTerminal(page: Page) {
  await page.route(/\/api\/v1\/commander\/workdir\/roots$/, (route) => route.fulfill({ json: [ROOT] }));
  await page.route(/\/api\/v1\/commander\/workdir\/list/, (route) => {
    const listed = new URL(route.request().url()).searchParams.get("path") ?? ROOT;
    return route.fulfill({
      json: { path: listed, entries: listed === ROOT ? [{ name: "teams", path: `${ROOT}/teams`, is_dir: true, hidden: false }] : [] },
    });
  });
  await page.routeWebSocket(/\/terminals\/[0-9a-f]+\/connect/, (ws) => {
    ws.send(JSON.stringify({ type: "history_reset", connectionGeneration: 1 }));
    ws.send(JSON.stringify({ type: "history_end", connectionGeneration: 1, sequence: 0, historyTotal: 0, historyPosition: 0 }));
  });
}

function leaderTeam(extra: Partial<TeamState["teams"][number]> = {}, tasks: Partial<Task>[] = []): TeamState {
  const full = tasks.map((t, i): Task => ({
    id: i + 1, title: `タスク${i + 1}`, goal: "目的の文", acceptance: "", files: [], assignee: "", status: "todo", report: null,
    created_at: 1, updated_at: 1, history: [], warnings: [], assignee_state: "", ...t,
  }));
  return {
    teams: [{
      id: "t0lead001", name: "ログイン班", created_at: 1, notify: true, members: [], tasks: full, roles: AUTO_ROLES,
      leader_effective: { kind: "headless", cli: "claude", account: "default", model: "", effort: "" },
      leader_run: { status: "done", tasks: full.length, dropped: ["役割「魔法使い」は既存の役割にないので捨てました"] },
      parallel: { running: 1, max: 3, waiting: 1, local_max: 1 },
      ...extra,
    }],
    dispatched: [], collected: [],
  };
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: エキスパート切で目標からチームを作り、板は題・状態・担当だけ → 入で詳細（自動の振り分け）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state: TeamState = { teams: [], dispatched: [], collected: [] };
    await mockTeams(page, state);
    await mockPickerAndTerminal(page); // 資料の選択（作業フォルダの API）
    await page.goto("/commander?view=team");

    // 一文を書くだけ（チームの画面の一番上の入力）。資料はパスだけを添える
    await page.getByLabel("チームリーダーに頼む").fill("ログイン機能を作りたい");
    const ask = page.locator("[data-team-ask]");
    await ask.locator('[data-material-add="dir"]').click();
    const materialPicker = page.getByRole("dialog", { name: "添えるフォルダを選ぶ" });
    await materialPicker.getByText("teams", { exact: true }).click();
    await materialPicker.getByRole("button", { name: "このフォルダを選択" }).click();
    await expect(ask.locator(`[data-team-material="${ROOT}/teams"]`)).toBeVisible();
    await page.getByRole("button", { name: "頼む", exact: true }).click();
    await expect.poll(() => state.autoPosts).toEqual([{ goal: "ログイン機能を作りたい", workdir: "", name: "", input_paths: [`${ROOT}/teams`] }]);
    await expect(page.locator("[data-team-inputs]")).toContainText("1 件");
    await expect(page.locator('[data-team-leader-run="running"]')).toContainText("チームリーダーが進めています");
    await noOverflow(page);

    // チームリーダーが計画して仕事を振った想定（板に待ち・作業中・上限の 3 件）
    const team = state.teams[0];
    team.leader_run = { status: "done", tasks: 3 };
    team.timeline = [{ at: 1764000000, kind: "progress", text: "計画を立てて 3 件の仕事を振りました" }];
    team.parallel = { running: 1, max: 3, waiting: 1, local_max: 1 };
    team.tasks = [
      { id: 1, title: "ログイン画面", goal: "", acceptance: "", files: ["src/login.py"], assignee: "", status: "todo", role: "r1", report: null, created_at: 1, updated_at: 1, history: [], warnings: ["#2 と src/login.py が重なっています"], leader_managed: true, tier: "implement" },
      { id: 2, title: "API", goal: "", acceptance: "", files: [], assignee: "", status: "doing", role: "r1", report: null, created_at: 1, updated_at: 1, history: [], warnings: [], run: { status: "running", last_line: "pytest -q", elapsed: 12, usage: { input_tokens: 100, output_tokens: 20, cost_usd: 0.1 } } },
      { id: 3, title: "試験", goal: "", acceptance: "", files: [], assignee: "", status: "doing", role: "r3", report: null, created_at: 1, updated_at: 1, history: [], warnings: [], limited_at: 1764000000, checks: ["pytest -q"] },
    ];
    await page.reload();

    // 切: 普段は時系列だけ（板・役割・設定・手入力のタスク作成は出ない）。頼み足すと leader/run へ
    await expect(page.locator('[data-commander-expert="off"]')).toBeVisible();
    await expect(page.locator("[data-team-timeline]")).toBeVisible();
    await expect(page.locator("[data-team-column]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "＋ タスク" })).toHaveCount(0);
    await page.getByLabel("チームリーダーに頼む").fill("パスワードの再設定も作って");
    await page.getByLabel(/このチームへ頼み足す/).check();
    await page.getByRole("button", { name: "頼む", exact: true }).click();
    await expect.poll(() => state.askPosts).toEqual([{ goal: "パスワードの再設定も作って" }]);
    state.teams[0].leader_run = { status: "done", tasks: 3 };
    await page.reload();
    await expect(page.locator("[data-team-roles-summary]")).toHaveCount(0);
    await expect(page.locator("[data-team-roles]")).toHaveCount(0);
    await expect(page.locator("[data-team-parallel]")).toHaveCount(0);
    await expect(page.locator("[data-team-leader]")).toHaveCount(0);
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: "候補の順番" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "チームリーダーを任命" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-expert-off.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-expert-off-dark.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "light" });

    // 入: 役割の詳細・自動の振り分けの状態・札・並列の数値・設定が出る
    await page.getByRole("switch", EXPERT_SWITCH).click();
    await expect(page.locator('[data-commander-expert="on"]')).toBeVisible();
    const role = page.locator('[data-team-role="実装"]');
    await expect(role).toContainText("プライマリ: Claude Code・opus・high");
    await expect(role).toContainText("セカンダリ: Codex・gpt-a・low");
    await expect(role.locator("[data-team-role-state]")).toContainText("セカンダリで動作中・プライマリは");
    await expect(role.locator("[data-team-role-state]")).toContainText(/\d\d:\d\d に回復/);
    await expect(page.locator('[data-team-role="試験"] [data-team-role-state]')).toContainText("担い手が未設定");
    await expect(page.locator("[data-team-parallel]")).toContainText("1／3");
    await expect(page.locator("[data-team-leader]")).toContainText("チームリーダー");
    await expect(page.getByRole("button", { name: "＋ タスク" })).toBeVisible();
    await expect(page.locator('[data-team-task="1"] [data-team-task-role]')).toContainText("実装");
    await expect(page.locator('[data-team-task="1"] [data-team-warning]')).toBeVisible();
    await expect(page.locator('[data-team-task="2"] [data-team-run-line]')).toContainText("pytest -q");
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: "候補の順番" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "チームリーダーを任命" })).toBeVisible();
    await page.keyboard.press("Escape");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-expert-on.png`, fullPage: true });
  });

  test(`AI チーム ${label}px: 担い手の選び手（コンソールとモデルが別・考えるレベル）と役割の保存`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = leaderTeam({}, [{ title: "ログイン画面", role: "r1" }]);
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "役割を編集（自動の振り分け）" }).click();
    const sheet = page.getByRole("dialog", { name: "役割を編集" });
    await sheet.locator('[data-role-edit="レビュー"]').getByRole("button", { name: "セカンダリを足す" }).click();
    const picker = sheet.locator("[data-role-endpoint-picker]");
    // 「コンソール」と「モデル」は別のかたまり
    await expect(picker.locator("[data-endpoint-console]")).toContainText("コンソール");
    await expect(picker.locator("[data-endpoint-model]")).toContainText("モデル");
    // まず使えるモデルの一覧（1 つ選ぶだけで CLI・接続・モデル・考えるレベルが決まる・まとまりごと）
    const choice = picker.locator("[data-endpoint-choice]");
    await expect(choice).toBeVisible();
    await expect(choice.locator("optgroup")).toHaveCount(5);
    await expect(choice.locator("optgroup").nth(0)).toHaveAttribute("label", "ローカル");
    await expect(choice.locator("optgroup").nth(1)).toHaveAttribute("label", "OpenCode（無料・Go）");
    await expect(choice.locator("optgroup").nth(4)).toHaveAttribute("label", "Codex");
    await choice.selectOption("claude:opus");
    await expect(picker.locator("[data-endpoint-console]").getByLabel("CLI")).toHaveValue("claude");
    await expect(picker.locator("[data-endpoint-model]").getByLabel(/モデルを直接指定/)).toHaveValue("opus");
    await expect(picker.locator("[data-endpoint-model]").getByLabel("考えるレベル")).toHaveValue("high");
    await expect(picker.locator("[data-endpoint-console]").getByLabel("種類")).toBeVisible();
    await expect(picker.locator("[data-endpoint-console]").getByLabel("CLI")).toBeVisible();
    await expect(picker.locator("[data-endpoint-model]").getByLabel(/モデルを直接指定/)).toBeVisible();
    await expect(picker.locator("[data-endpoint-model]").getByLabel("考えるレベル")).toBeVisible();
    // codex を選ぶと、そのモデルの一覧と考えるレベルが自動で出る
    await picker.locator("[data-endpoint-console]").getByLabel("種類").selectOption("console");
    await picker.locator("[data-endpoint-console]").getByLabel("CLI").selectOption("codex");
    await expect.poll(() => state.modelsReqs).toContain("codex/default");
    await picker.getByLabel(/モデルを直接指定/).fill("gpt-a");
    await expect(picker.locator("datalist option")).toHaveCount(1);
    await expect(picker.getByLabel("考えるレベル").locator("option")).toHaveText(["既定", "low", "high"]);
    await picker.getByLabel("考えるレベル").selectOption("high");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-endpoint-picker.png`, fullPage: true });
    await picker.getByRole("button", { name: "セカンダリに足す" }).click();
    await expect(sheet.locator('[data-role-edit="レビュー"]')).toContainText("Codex・gpt-a・high・画面あり");
    await sheet.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => state.rolesPuts?.length).toBe(1);
    const review = state.rolesPuts![0].find((r) => r.name === "レビュー") as { secondary: Record<string, string>[] };
    expect(review.secondary).toEqual([{ kind: "console", cli: "codex", account: "default", model: "gpt-a", effort: "high" }]);

    // チームリーダーの任命（ヘッドレス固定の選び手）
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: "チームリーダーを任命" }).click();
    const leader = page.getByRole("dialog", { name: "チームリーダーを任命" });
    await expect(leader).toContainText("目標を計画してタスクを配る AI");
    await expect(leader.getByLabel("種類")).toHaveCount(0);
    await leader.getByLabel("CLI").selectOption("claude");
    await leader.getByLabel(/モデルを直接指定/).fill("opus");
    await leader.getByLabel("考えるレベル").selectOption("xhigh");
    await leader.getByRole("button", { name: "任命する" }).click();
    await expect.poll(() => (state.teams[0].leader as { model?: string } | undefined)?.model).toBe("opus");
    expect(state.teams[0].leader).toMatchObject({ kind: "headless", cli: "claude", effort: "xhigh" });
  });

  test(`AI チーム ${label}px: エキスパート切でも引き継ぎ先を選べる（タスクのカードとコンソールのカード）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = leaderTeam({ members: [{ session_id: "aa110001", role: "developer" }] }, [
      { title: "ログイン画面", role: "r1", assignee: "aa110001", status: "blocked", blocked_reason: "no_handoff", assignee_state: "waiting" },
    ]);
    await mockTeams(page, state);
    await mockPickerAndTerminal(page); // 後に登録したほうが先に効く
    await page.goto("/commander?view=team");

    // 切: 要対応（一番上）に「引き継ぎ先がありません」と選ぶボタン
    await expect(page.locator('[data-team-attention] [data-team-stuck="1"]')).toContainText("引き継ぎ先がありません");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-no-handoff.png`, fullPage: true });
    await page.locator('[data-team-handoff-open="1"]').click();
    const sheet = page.getByRole("dialog", { name: "引き継ぎ先を選ぶ" });
    // 切: 使えるモデルの一覧から 1 つ選ぶだけ（細かい指定は出ない）
    await expect(sheet.locator("[data-endpoint-console]")).toHaveCount(0);
    const choice = sheet.locator("[data-endpoint-choice]");
    await expect(choice).toBeVisible();
    await choice.selectOption("codex:gpt");
    await sheet.getByLabel(/セカンダリにも追加/).check();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-handoff-sheet.png`, fullPage: true });
    await sheet.getByRole("button", { name: "引き継いで再開" }).click();
    await expect.poll(() => state.handoffPosts?.length).toBe(1);
    expect(state.handoffPosts![0]).toEqual({
      endpoint: { kind: "headless", cli: "codex", account: "default", model: "gpt-a", effort: "" }, add_to_secondary: true,
    });

    // コンソールのカード（もう一度止まった状態に戻して）
    state.teams[0].tasks[0].status = "blocked";
    state.teams[0].tasks[0].blocked_reason = "no_handoff";
    await page.goto("/commander");
    const button = page.locator("[data-console-handoff]").first();
    await expect(button).toBeVisible();
    await expect(button).toContainText("引き継ぎ先を選ぶ");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-console-handoff.png`, fullPage: true });
    await button.click();
    await expect(page.getByRole("dialog", { name: "引き継ぎ先を選ぶ" })).toBeVisible();
  });

  test(`AI チーム ${label}px: チームの既定（チームリーダー）と保存先を一度だけ選ぶ`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = leaderTeam();
    await mockTeams(page, state);
    await mockPickerAndTerminal(page); // 後に登録したほうが先に効く
    await page.goto("/commander?view=team");

    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "チームの既定（チームリーダー・保存先）" }).click();
    const sheet = page.getByRole("dialog", { name: "チームの既定" });
    // 既定のチームリーダー（使えるモデルの一覧から 1 つ選ぶ）
    await expect(sheet.locator("[data-endpoint-console]")).toBeVisible();
    await sheet.locator("[data-endpoint-choice]").first().selectOption("codex:gpt");
    await sheet.getByRole("button", { name: "既定のチームリーダーにする" }).click();
    await expect.poll(() => state.defaultsPuts?.length).toBe(1);
    expect(state.defaultsPuts![0]).toEqual({ default_leader: { kind: "headless", cli: "codex", account: "default", model: "gpt-a", effort: "" } });
    // 保存先が未設定 → 同じ所で一度だけ選ぶ
    await expect(sheet.locator("[data-team-root]")).toContainText("未設定");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-team-root-unset.png`, fullPage: true });
    await sheet.getByRole("button", { name: "保存先を選ぶ" }).click();
    const picker = page.getByRole("dialog", { name: "チームの保存先を選択" });
    await picker.getByText("teams", { exact: true }).click();
    await picker.getByRole("button", { name: "このフォルダを選択" }).click();
    await expect.poll(() => state.defaultsPuts?.length).toBe(2);
    expect(state.defaultsPuts![1]).toEqual({ team_root: `${ROOT}/teams` });
    await expect(sheet.locator("[data-team-root-path]")).toHaveText(`${ROOT}/teams`);
    // エキスパート入のときだけ「変える」が出る
    await expect(sheet.locator("[data-team-root]").getByRole("button", { name: "変える" })).toBeVisible();
    await expect(sheet.locator("[data-default-roles]")).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-team-root-set-expert.png`, fullPage: true });
    // 切にして開き直すと、決めた保存先の表示だけ（変えるボタンも役割の編集も出ない）
    await page.keyboard.press("Escape");
    await page.getByRole("switch", EXPERT_SWITCH).click();
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "チームの既定（チームリーダー・保存先）" }).click();
    const again = page.getByRole("dialog", { name: "チームの既定" });
    await expect(again.locator("[data-team-root-path]")).toHaveText(`${ROOT}/teams`);
    await expect(again.locator("[data-team-root]").getByRole("button", { name: "変える" })).toHaveCount(0);
    await expect(again.locator("[data-default-roles]")).toHaveCount(0);
    await expect(again.getByLabel(/モデルを直接指定/)).toHaveCount(0);
    await expect(again.locator("[data-endpoint-console]")).toHaveCount(0);
    // 切でも使えるモデルの一覧（1 つ選ぶだけ）は残る
    await expect(again.locator("[data-endpoint-choice]")).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-team-root-set-off.png`, fullPage: true });
  });
}

// ---------------------------------------------------------------- T3c: 一文で頼む → 時系列（依頼・計画・進み具合・質問・結果・取り込む）

/** チームリーダーが計画して振り、質問が 1 つ待っていて、結果が出た想定のチーム。 */
function timelineTeam(): TeamState {
  const task = (id: number, title: string, tier: string, status: string, extra: Partial<Task> = {}): Task => ({
    id, title, goal: "", acceptance: "", files: [], assignee: "", status, report: null, created_at: 1, updated_at: 1, history: [], warnings: [],
    assignee_state: "", leader_managed: true, tier, ...extra,
  });
  const state = leaderTeam({
    id: "t0tl00001", name: "ログイン機能", leader_run: { status: "done", endpoint: "Claude Code", n: 1 },
    requests: [{ at: 1764000000, text: "ログイン機能を作って、試験まで通して" }],
    plan: {
      markdown: "# 計画\n1. 現状を調べる\n2. 画面と API を並列で作る\n3. 評価", updated_at: 1764000100,
      items: [
        { id: "scan", title: "現状を調べる", tier: "explore", depends_on: [], files: [], checks: [], status: "done", task_id: 1 },
        { id: "ui", title: "画面を作る", tier: "implement", depends_on: ["scan"], files: [], checks: ["ls"], status: "integrated", task_id: 2 },
        { id: "api", title: "API を作る", tier: "implement", depends_on: ["scan"], files: [], checks: [], status: "delegated", task_id: 3 },
        { id: "eval", title: "全体の評価", tier: "review", depends_on: ["ui", "api"], files: [], checks: [], status: "planned", task_id: null },
      ],
    },
    timeline: [
      { at: 1764000000, kind: "request", text: "依頼: ログイン機能を作って、試験まで通して" },
      { at: 1764000100, kind: "plan", text: "計画を更新（4 件）" },
      { at: 1764000200, kind: "progress", text: "現状を調べ終えました。画面と API を並列で作ります" },
      { at: 1764000300, kind: "integrate", text: "仕事 #2「画面を作る」を統合しました" },
      { at: 1764000400, kind: "ask", text: "利用者への質問: パスワードの強度の規則はどうしますか" },
    ],
    questions: [{ id: "q1", question: "パスワードの強度の規則はどうしますか", options: ["8 文字以上", "12 文字以上"], asked_at: 1764000400, answer: null, answered_at: null }],
    mcp_log: [{ at: 1, tool: "team_status", ok: true, seconds: 0.01 }, { at: 2, tool: "delegate", ok: true, seconds: 0.4 }],
    usage_total: { input_tokens: 123456, output_tokens: 7890, cost_usd: 1.23 },
    git: { enabled: true, origin_was_git: true, base_branch: "main", team_branch: "nexarch/t0tl00001" }, autonomy: "ask",
    members: [{ kind: "headless", id: "h0000bbbb", role: "developer", cli: "codex", model: "gpt-a", label: "実装の係" }],
    result: {
      summary: "画面と API を作り、統合して確認しました", outcome: "done", finished_at: 1764000900, remaining: ["README の更新"],
      checks: { status: "passed", ran_at: 1764000800, commands: ["pytest -q"] },
      changes: { files: ["src/login.py", "src/api.py", "tests/test_login.py"], count: 3, added: 120, removed: 4 },
      usage: { input_tokens: 123456, output_tokens: 7890, cost_usd: 1.23 }, can_land: true, landed: false,
      team_branch: "nexarch/t0tl00001", base_branch: "main",
    },
  }, [
    task(1, "現状を調べる", "explore", "done"),
    task(2, "画面を作る", "implement", "done", { branch: "nexarch/t0tl00001-2" }),
    task(3, "API を作る", "implement", "doing", { branch: "nexarch/t0tl00001-3", assignee: "h0000bbbb", assignee_state: "working" }),
  ]);
  return state;
}

for (const [label, width, height] of [["320", 320, 800], ["1280", 1280, 900]] as const) {
  test(`AI チーム ${label}px: エキスパート切で一文の依頼 → 時系列（計画・進み具合・質問に答える・結果・取り込む）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = timelineTeam();
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    // 一番上: 一文の入力。要対応（質問）がその次
    const ask = page.locator("[data-team-ask]");
    await expect(ask).toBeVisible();
    const top = await ask.boundingBox();
    const attention = page.locator("[data-team-attention]");
    await expect(attention.locator("[data-team-question]")).toContainText("パスワードの強度の規則はどうしますか");
    expect((await attention.boundingBox())!.y).toBeGreaterThan(top!.y);
    // 時系列: 依頼 → 計画（題の一覧。開くと本文）→ 進み具合 → 結果。切では「詳しく」に畳まれている（質問・結果は畳まれない）
    await expect(page.locator("[data-team-more]")).toHaveJSProperty("open", false);
    await expect(page.locator("[data-team-plan]")).toBeHidden();
    await page.locator("[data-team-more-toggle]").click();
    await expect(page.locator("[data-team-request]")).toContainText("ログイン機能を作って、試験まで通して");
    const plan = page.locator("[data-team-plan]");
    await expect(plan).toContainText("現状を調べる");
    await expect(plan).toContainText("API を作る");
    await plan.getByText("計画の本文").click();
    await expect(plan).toContainText("画面と API を並列で作る");
    await expect(page.locator('[data-team-progress-task="1"]')).toContainText("調べ済み");
    await expect(page.locator('[data-team-progress-task="2"]')).toContainText("統合済み");
    await expect(page.locator('[data-team-progress-task="3"]')).toContainText("実装中");
    await expect(page.locator("[data-team-progress]")).toContainText("現状を調べ終えました");
    // アバターと 6 段の図: どこまで進んだかが一目で分かる（進んでいる段だけ控えめに動く）
    const mission = page.locator("[data-team-mission]");
    await expect(mission.locator("[data-mission-stage]")).toHaveCount(6);
    await expect(mission.locator('[data-mission-stage="explore"]')).toHaveAttribute("data-mission-state", "done");
    await expect(mission.locator('[data-mission-stage="plan"]')).toHaveAttribute("data-mission-state", "done");
    await expect(mission.locator('[data-mission-stage="implement"]')).toHaveAttribute("data-mission-state", "active");
    await expect(mission.locator('[data-mission-stage="verify"]')).toHaveAttribute("data-mission-state", "done");
    await expect(mission.locator("[data-mission-summary]")).toContainText("実装を 1 人が進めています");
    await expect(mission.locator('[data-mission-member="h0000bbbb"]')).toContainText("実装の係");
    // 段を押すとその段の仕事が開く（もう一度押すと閉じる）
    await mission.locator('[data-mission-stage="implement"]').click();
    await expect(mission.locator('[data-mission-stage-tasks="implement"]')).toContainText("API を作る");
    await mission.locator('[data-mission-stage="implement"]').click();
    await expect(mission.locator('[data-mission-stage-tasks="implement"]')).toHaveCount(0);
    await expect(page.locator('[data-team-result="done"]')).toContainText("画面と API を作り、統合して確認しました");
    await expect(page.locator("[data-team-result-checks]")).toHaveText("合格");
    await expect(page.locator("[data-team-result-changes]")).toContainText("3 件");
    // 切: 板・MCP の履歴・手入力のタスク作成は出ない
    await expect(page.locator("[data-team-column]")).toHaveCount(0);
    await expect(page.locator("[data-team-expert-details]")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "＋ タスク" })).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-timeline.png`, fullPage: true });

    // 質問にその場で答える（選択肢のボタン）
    await attention.getByRole("button", { name: "12 文字以上" }).click();
    await expect.poll(() => state.answers).toEqual([{ id: "q1", answer: "12 文字以上" }]);
    // 取り込む（元から git の作業場所だけ）
    await page.getByRole("button", { name: "取り込む", exact: true }).click();
    await expect.poll(() => state.leaderCalls).toEqual(["land"]);
    await expect(page.locator("[data-team-landed]")).toContainText("取り込み済み");
    await expect(page.locator("[data-team-land]")).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-timeline-landed.png`, fullPage: true });
  });

  test(`AI チーム ${label}px: エキスパート入で板・MCP の呼び出しの履歴・使用量が出る（止まったら再開）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = timelineTeam();
    state.teams[0].leader_run = { status: "failed", endpoint: "Claude Code", reason: "finish されないまま 3 回再開しました" };
    await mockTeams(page, state);
    await page.goto("/commander?view=team");

    await expect(page.locator("[data-team-column]").first()).toBeVisible();
    const details = page.locator("[data-team-expert-details]");
    await details.locator("summary").click();
    await expect(details).toContainText("聞いてから進める");
    await expect(details).toContainText("nexarch/t0tl00001");
    await expect(details).toContainText("123,456 入力");
    await expect(details.locator("[data-team-mcp-log]")).toContainText("delegate");
    // 止まったら、ここまでの状態から再開できる
    await page.locator("[data-team-resume]").click();
    await expect.poll(() => state.leaderCalls).toEqual(["resume"]);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-timeline-expert.png`, fullPage: true });
  });

  test(`AI チーム ${label}px: エキスパート切でも担い手の状態（作業中・待ち・止まった・済み）が色・形・文字で分かる`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = timelineTeam();
    const team = state.teams[0];
    const task = (id: number, title: string, status: string, assignee: string, extra: Partial<Task> = {}): Task => ({
      id, title, goal: "", acceptance: "", files: [], assignee, status, report: null, created_at: 1, updated_at: 1, history: [], warnings: [],
      assignee_state: "", leader_managed: true, tier: "implement", ...extra,
    });
    team.members = [
      { kind: "headless", id: "h0000bbbb", role: "developer", cli: "codex", model: "gpt-a", label: "実装の係" },
      { kind: "headless", id: "h0000cccc", role: "reviewer", cli: "claude", model: "opus", label: "評価の係" },
      { kind: "headless", id: "h0000dddd", role: "researcher", cli: "claude", model: "sonnet", label: "調査の係" },
      { kind: "headless", id: "h0000eeee", role: "developer", cli: "codex", model: "gpt-b", label: "難しい係" },
    ];
    team.tasks = [
      task(1, "現状を調べる", "done", "h0000dddd", { tier: "explore", report: { summary: "調査を終えました", changed_files: [], tests: "", remaining: "" } }),
      task(3, "API を作る", "doing", "h0000bbbb", { run: { run_id: "r1", status: "running", member: "h0000bbbb", cli: "codex", started_at: 1, last_line: "pytest -q を実行中", progress: [], usage: { input_tokens: 1200, output_tokens: 300, cost_usd: 0.05 }, tail: "", exit_code: null } }),
      task(4, "全体を評価する", "doing", "h0000cccc", { tier: "review", limited_at: 1764000000 }),
      task(5, "移行を直す", "blocked", "h0000eeee"),
    ];
    await mockTeams(page, state);
    await page.goto("/commander?view=team");
    const mission = page.locator("[data-team-mission]");
    const expectStatus = async (id: string, kind: string, tag: string, ring: string) => {
      const m = mission.locator(`[data-mission-member="${id}"]`);
      await expect(m).toHaveAttribute("data-mission-status", kind);
      await expect(m.locator("[data-status-tag]")).toHaveText(tag);          // 文字
      await expect(m.locator("[data-avatar-ring]")).toHaveAttribute("data-avatar-ring", ring);  // 形・動き
      return m;
    };
    // 動いている: 青の破線の輪がゆっくり回る（印は付かない）
    const working = await expectStatus("h0000bbbb", "working", "作業中", "working");
    expect(await working.locator("[data-avatar-ring] > span").first().evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");
    await expect(working.locator("svg")).toHaveCount(0);
    // 待っている: 琥珀・砂時計・輪は回らない
    const waiting = await expectStatus("h0000cccc", "waiting", "待ち", "waiting");
    expect(await waiting.locator("[data-avatar-ring] > span").first().evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    expect(await waiting.locator("[data-avatar-ring] > span").first().evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("solid");
    await expect(waiting.locator("svg")).toHaveCount(1);
    // 止まった: 赤の実線の輪・斜線と ×（エキスパート切でも常に出る）
    const stopped = await expectStatus("h0000eeee", "stopped", "止まった", "stopped");
    expect(await stopped.locator("[data-avatar-ring] > span").first().evaluate((el) => getComputedStyle(el).borderTopColor)).toMatch(/^(rgb|oklch|color)/);
    await expect(stopped.locator("svg")).toHaveCount(2);  // 斜線 + ×の印
    // 終わった: 緑・チェックの印
    const done = await expectStatus("h0000dddd", "done", "済み", "done");
    await expect(done.locator("svg")).toHaveCount(1);
    // 色だけに頼らない: 4 つの札の文字・輪の形・印の数がすべて違う
    // エキスパート切では CLI・モデルは出ない。押すと詳細の中に出る
    await expect(working).not.toContainText("gpt-a");
    await working.click();
    const details = mission.locator('[data-mission-member-details="h0000bbbb"]');
    await expect(details).toContainText("API を作る");
    await expect(details.locator("[data-mission-member-last]")).toContainText("pytest -q を実行中");
    await expect(details.locator("[data-mission-member-cli]")).toContainText("gpt-a");
    await expect(details.locator("[data-mission-member-usage]")).toContainText("1,200 入力");
    await working.click();
    // 止まった仕事の要対応は切のままでも出ている
    await expect(page.locator("[data-team-stuck]")).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-mission-${label}-off-light.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-mission-${label}-off-dark.png`, fullPage: true });
    // エキスパート入: モデルが札の下に出る
    await page.getByRole("switch", EXPERT_SWITCH).click();
    await expect(mission.locator('[data-mission-member="h0000bbbb"]')).toContainText("gpt-a");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-mission-${label}-on-dark.png`, fullPage: true });
    await page.emulateMedia({ colorScheme: "light" });
    await page.screenshot({ path: `test-results/commander-team-mission-${label}-on-light.png`, fullPage: true });
  });

  test(`AI チーム ${label}px: 資料を添えてこのチームへ頼む・メンバーの状態を開く・チームの前提を切り替える`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = timelineTeam();
    await mockTeams(page, state);
    await mockPickerAndTerminal(page); // 資料の選択（作業フォルダの API）
    await page.goto("/commander?view=team");

    // メンバーのアバターを押すと、その人の状態と持っている仕事が開く
    const mission = page.locator("[data-team-mission]");
    await mission.locator('[data-mission-member="h0000bbbb"]').click();
    const member = mission.locator('[data-mission-member-details="h0000bbbb"]');
    await expect(member).toContainText("実装の係");
    await expect(member.locator("[data-mission-member-state]")).toContainText("作業中");
    await expect(member).toContainText("API を作る");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-mission-member.png`, fullPage: true });

    // 資料（フォルダ）を添えて、このチームへ頼み足す（中身は送らずパスだけ）
    const ask = page.locator("[data-team-ask]");
    await ask.locator('[data-material-add="dir"]').click();
    const picker = page.getByRole("dialog", { name: "添えるフォルダを選ぶ" });
    await picker.getByText("teams", { exact: true }).click();
    await picker.getByRole("button", { name: "このフォルダを選択" }).click();
    const chip = ask.locator(`[data-team-material="${ROOT}/teams"]`);
    await expect(chip).toBeVisible();
    // 札から外せる
    await chip.getByRole("button", { name: `${ROOT}/teams を外す` }).click();
    await expect(chip).toHaveCount(0);
    // 選び直してから頼む
    await ask.locator('[data-material-add="dir"]').click();
    const pickerAgain = page.getByRole("dialog", { name: "添えるフォルダを選ぶ" });
    await pickerAgain.getByText("teams", { exact: true }).click();
    await pickerAgain.getByRole("button", { name: "このフォルダを選択" }).click();
    await expect(ask.locator(`[data-team-material="${ROOT}/teams"]`)).toBeVisible();
    await page.getByLabel("チームリーダーに頼む").fill("資料を見て直して");
    await ask.getByLabel(/このチームへ頼み足す/).check();
    await page.getByRole("button", { name: "頼む", exact: true }).click();
    await expect.poll(() => state.askPosts?.at(-1)).toEqual({ goal: "資料を見て直して", input_paths: [`${ROOT}/teams`] });
    await expect(page.locator("[data-team-inputs]")).toContainText("1 件");
    await noOverflow(page);

    // チームの前提（閉じたチーム・再起動の後の自動再開）を既存の操作メニューから切り替える
    await page.getByRole("button", { name: "チームの操作" }).click();
    await page.getByRole("menuitem", { name: /閉じたチーム/ }).click();
    await expect.poll(() => state.teamPuts).toContainEqual({ local_only: true });
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /閉じたチーム.*オン/ })).toBeVisible();
    await page.getByRole("menuitem", { name: /再起動の後の自動再開/ }).click();
    await expect.poll(() => state.teamPuts).toContainEqual({ auto_resume: false });
    await page.getByRole("button", { name: "チームの操作" }).click();
    await expect(page.getByRole("menuitem", { name: /再起動の後の自動再開.*オフ/ })).toBeVisible();
    await page.keyboard.press("Escape");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/commander-team-${label}-team-settings.png`, fullPage: true });
  });
}
