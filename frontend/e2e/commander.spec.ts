/** AI Commander（/commander）の E2E。API と端末の WebSocket はモック（実 CLI を動かさない）。
 *
 * 目的（c1-spec.md「E2E」の項目）:
 * - ＋ コンソールのシートの流れ（未導入の CLI は選べない・アカウント・作業フォルダ・個数・表示名）
 * - タイル（1280px）とモバイル 320px の 1 つずつ（リスト → 全画面）
 * - アカウントを作る・消す（確認ダイアログ）
 * - ロック（鍵の切り替え・ロック中は閉じるが無効・一括はロック中を飛ばす・409 の知らせ）
 * - 自動で戻す（全体）の設定（エキスパートのときだけ出す・切った設定を覚える）
 * - 320 と 1280 のライト・ダークの撮影
 *
 * 根拠: 2026-11-28 利用者「Nexarch のコンソールモードでは、開いているコンソールごとに、ロックをかけるようにしてほしい」
 * （勝手に落とされるのを防ぐ）。ロックの API と状態はモックで確かめ、本物の tmux には触らない。
 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const ROOT = "/srv/code";

interface CliFixture {
  id: string;
  name: string;
  executable: string;
  installed: boolean;
  multi_account: boolean;
  connector?: string;
}

const CLIS: CliFixture[] = [
  { id: "claude", name: "Claude Code", executable: "claude", installed: true, multi_account: true },
  { id: "codex", name: "Codex CLI", executable: "codex", installed: true, multi_account: true },
  { id: "opencode", name: "OpenCode", executable: "opencode2", installed: true, multi_account: false, connector: "controldeck-opencode" },
  { id: "gemini", name: "Gemini CLI", executable: "gemini", installed: false, multi_account: false },
];

interface AccountFixture {
  name: string;
  default: boolean;
  logged_in: boolean | null;
  label: string;
  title?: string;
  usage?: { five_hour: { used_percent: number; resets_at: number | null } | null;
    seven_day: { used_percent: number; resets_at: number | null } | null } | null;
  usage_statusline?: "ours" | "other" | "none";
  login_name?: string;
}

interface ConsoleFixture {
  id: string;
  name: string;
  created_at: number;
  attached: boolean;
  persistent: boolean;
  engine: "v1";
  program: string;
  cwd: string;
  pid: number;
  activity_at: number;
  alive: boolean;
  workload: "idle" | "running";
  commander_cli: string;
  commander_account: string;
  commander_label?: string;
  commander_account_label?: string;
  commander_state?: "working" | "waiting" | "limited" | "error" | "exited";
  commander_last_line?: string;
  commander_detail?: string;
  commander_autoresume?: string;
  commander_resume_at?: string;
  commander_resume_message?: string;
  commander_locked?: boolean;
  commander_usage?: { five_hour: { used_percent: number; resets_at: number | null } | null;
    seven_day: { used_percent: number; resets_at: number | null } | null };
}

function consoleFixture(overrides: Partial<ConsoleFixture> & Pick<ConsoleFixture, "id">): ConsoleFixture {
  return {
    name: `cdterm-${overrides.id}`,
    created_at: 1764000000,
    attached: false,
    persistent: true,
    engine: "v1",
    program: "bash",
    cwd: "~/CodeDEV/Conv",
    pid: 4242,
    activity_at: 1764000500,
    alive: true,
    workload: "idle",
    commander_cli: "claude",
    commander_account: "default",
    ...overrides,
  };
}

interface CommanderState {
  consoles: ConsoleFixture[];
  accounts: Record<string, AccountFixture[]>;
  consolePosts: Record<string, unknown>[];
  consoleDeletes: string[];
  accountPosts: { cli: string; name: string }[];
  accountDeletes: string[];
  labelPuts: { cli: string; name: string; label: string }[];
  folderPosts: string[];
  autoresumePuts: { id: string; enabled: boolean }[];
  resumeMessage: string;
  consoleMessagePuts: { id: string; message: string }[];
  statuslinePuts: { name: string; enabled: boolean }[];
  lockPuts: { id: string; locked: boolean }[];
  autoRestore: boolean;
  bulkDeletes: number;
  /** 閉じる API を 409（ロック中）にする。画面が古いときの知らせを確かめる。 */
  forceConflict: boolean;
}

function initialState(): CommanderState {
  return {
    consoles: [
      consoleFixture({
        id: "aa110001", program: "claude", commander_cli: "claude",
        commander_account: "default", commander_label: "調査",
        commander_state: "working", commander_last_line: "· Drizzling… (2m 39s · ↓ 16k tokens)",
        commander_usage: {
          five_hour: { used_percent: 82, resets_at: Math.floor(Date.now() / 1000) + 2 * 3600 + 10 * 60 + 30 },
          seven_day: { used_percent: 40, resets_at: Math.floor(Date.now() / 1000) + 3 * 86400 + 5 * 3600 + 30 },
        },
      }),
      consoleFixture({
        id: "aa110002", program: "opencode", cwd: "~/CodeDEV/Hello",
        commander_cli: "opencode", commander_account: "default", commander_account_label: "ControlDeck の API",
        commander_state: "limited", commander_last_line: "Build · DeepSeek",
        commander_detail: "Error: 429 Too Many Requests — rate limit exceeded",
        commander_resume_at: String(Math.floor(new Date(2026, 10, 27, 22, 21).getTime() / 1000)),
      }),
    ],
    accounts: {
      claude: [
        { name: "default", default: true, logged_in: true, label: "", usage: {
          five_hour: { used_percent: 12, resets_at: Math.floor(Date.now() / 1000) + 3600 + 60 }, seven_day: null },
          usage_statusline: "none" },
        { name: "work", default: false, logged_in: false, label: "", usage_statusline: "other" },
      ],
      codex: [{ name: "default", default: true, logged_in: null, label: "", login_name: "ChatGPT でログイン" }],
      opencode: [
        { name: "default", default: true, logged_in: null, label: "ControlDeck の API", title: "OpenCode の設定のモデル（auto・ゲートウェイ）" },
        { name: "api-1a2b3c4d5e", default: false, logged_in: null, label: "", title: "DeepSeek V4.1 Flash（openrouter）" },
        { name: "api-9f8e7d6c5b", default: false, logged_in: null, label: "", title: "OpenRouter / nvidia/nemotron-3.5-super-vl:free-with-a-very-long-identifier（openrouter）" },
      ],
      gemini: [{ name: "default", default: true, logged_in: null, label: "" }],
    },
    consolePosts: [],
    consoleDeletes: [],
    accountPosts: [],
    accountDeletes: [],
    labelPuts: [],
    folderPosts: [],
    autoresumePuts: [],
    resumeMessage: "続けてください",
    consoleMessagePuts: [],
    statuslinePuts: [],
    lockPuts: [],
    autoRestore: true,
    bulkDeletes: 0,
    forceConflict: false,
  };
}

/** commander の API と端末の WebSocket をモックする。端末は履歴0・入力待ちの「Live」になる。 */
async function mockCommander(page: Page, state: CommanderState) {
  await page.route(/\/api\/v1\/commander\//, (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1/commander/", "");
    const method = request.method();
    if (path === "clis" && method === "GET") {
      return route.fulfill({ json: { clis: CLIS } });
    }
    if (path === "consoles" && method === "GET") {
      return route.fulfill({ json: { consoles: state.consoles } });
    }
    if (path === "consoles" && method === "POST") {
      const body = request.postDataJSON() as { cli: string; account: string; count: number; label: string };
      state.consolePosts.push(body);
      const opened = Array.from({ length: body.count }, (_, index) => consoleFixture({
        id: `bb22000${index + 1}`,
        program: body.cli,
        commander_cli: body.cli,
        commander_account: body.account,
        commander_label: body.label,
      }));
      for (const item of opened) state.consoles.push(item);
      return route.fulfill({ status: 201, json: { opened, not_opened: 0, reason: "" } });
    }
    if (path === "teams" && method === "GET") return route.fulfill({ json: { teams: [] } });
    if (path === "settings" && method === "GET") {
      return route.fulfill({ json: {
        resume_message: state.resumeMessage, default_resume_message: "続けてください", auto_restore: state.autoRestore,
      } });
    }
    if (path === "settings" && method === "PUT") {
      const body = request.postDataJSON() as { resume_message?: string; auto_restore?: boolean };
      if (body.resume_message !== undefined) state.resumeMessage = body.resume_message || "続けてください";
      if (body.auto_restore !== undefined) state.autoRestore = body.auto_restore;
      return route.fulfill({ json: {
        resume_message: state.resumeMessage, default_resume_message: "続けてください", auto_restore: state.autoRestore,
      } });
    }
    const lockMatch = path.match(/^consoles\/([0-9a-f]+)\/lock$/);
    if (lockMatch && method === "PUT") {
      const body = request.postDataJSON() as { locked: boolean };
      state.lockPuts.push({ id: lockMatch[1], locked: body.locked });
      state.consoles = state.consoles.map((item) => (item.id === lockMatch[1]
        ? { ...item, commander_locked: body.locked } : item));
      return route.fulfill({ json: { ok: true, locked: body.locked } });
    }
    const messageMatch = path.match(/^consoles\/([0-9a-f]+)\/resume-message$/);
    if (messageMatch && method === "PUT") {
      const message = (request.postDataJSON() as { message: string }).message;
      state.consoleMessagePuts.push({ id: messageMatch[1], message });
      state.consoles = state.consoles.map((item) => (item.id === messageMatch[1]
        ? { ...item, commander_resume_message: message || undefined } : item));
      return route.fulfill({ json: { ok: true, message } });
    }
    const autoresumeMatch = path.match(/^consoles\/([0-9a-f]+)\/autoresume$/);
    if (autoresumeMatch && method === "PUT") {
      const body = request.postDataJSON() as { enabled: boolean };
      state.autoresumePuts.push({ id: autoresumeMatch[1], enabled: body.enabled });
      state.consoles = state.consoles.map((item) => (item.id === autoresumeMatch[1]
        ? { ...item, commander_autoresume: body.enabled ? undefined : "off" } : item));
      return route.fulfill({ json: { ok: true, enabled: body.enabled } });
    }
    if (path === "consoles" && method === "DELETE") {
      // 一括で閉じる: ロック中は飛ばす（その数を返す）
      const before = state.consoles.length;
      state.consoles = state.consoles.filter((item) => item.commander_locked);
      state.bulkDeletes += 1;
      return route.fulfill({ json: {
        closed: before - state.consoles.length, skipped_locked: state.consoles.length,
      } });
    }
    const consoleMatch = path.match(/^consoles\/([0-9a-f]+)$/);
    if (consoleMatch && method === "DELETE") {
      if (state.forceConflict || state.consoles.find((item) => item.id === consoleMatch[1])?.commander_locked) {
        return route.fulfill({ status: 409, json: { detail: "locked" } });
      }
      state.consoleDeletes.push(consoleMatch[1]);
      state.consoles = state.consoles.filter((item) => item.id !== consoleMatch[1]);
      return route.fulfill({ json: { ok: true } });
    }
    const accountsMatch = path.match(/^accounts\/([a-z]+)$/);
    if (accountsMatch && method === "GET") {
      return route.fulfill({ json: { accounts: state.accounts[accountsMatch[1]] ?? [] } });
    }
    if (accountsMatch && method === "POST") {
      const body = request.postDataJSON() as { name: string };
      state.accountPosts.push({ cli: accountsMatch[1], name: body.name });
      state.accounts[accountsMatch[1]] = [
        ...(state.accounts[accountsMatch[1]] ?? []),
        { name: body.name, default: false, logged_in: false, label: "" },
      ];
      return route.fulfill({ status: 201, json: { name: body.name, logged_in: false } });
    }
    const statuslineMatch = path.match(/^accounts\/claude\/([a-z0-9_-]+)\/usage-statusline$/);
    if (statuslineMatch && method === "PUT") {
      const enabled = (request.postDataJSON() as { enabled: boolean }).enabled;
      state.statuslinePuts.push({ name: statuslineMatch[1], enabled });
      state.accounts.claude = state.accounts.claude.map((item) => (item.name === statuslineMatch[1]
        ? { ...item, usage_statusline: enabled ? "ours" : "none" } : item));
      return route.fulfill({ json: { name: statuslineMatch[1], usage_statusline: enabled ? "ours" : "none" } });
    }
    const labelMatch = path.match(/^accounts\/([a-z]+)\/([a-z0-9_-]+)\/label$/);
    if (labelMatch && method === "PUT") {
      const body = request.postDataJSON() as { label: string };
      state.labelPuts.push({ cli: labelMatch[1], name: labelMatch[2], label: body.label });
      state.accounts[labelMatch[1]] = (state.accounts[labelMatch[1]] ?? []).map(
        (item) => (item.name === labelMatch[2] ? { ...item, label: body.label } : item),
      );
      return route.fulfill({ json: { name: labelMatch[2], label: body.label } });
    }
    const deleteMatch = path.match(/^accounts\/([a-z]+)\/([a-z0-9_-]+)$/);
    if (deleteMatch && method === "DELETE") {
      state.accountDeletes.push(`${deleteMatch[1]}/${deleteMatch[2]}`);
      state.accounts[deleteMatch[1]] = (state.accounts[deleteMatch[1]] ?? []).filter(
        (item) => item.name !== deleteMatch[2],
      );
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ status: 404, json: { detail: `unmocked: ${method} ${path}` } });
  });
  // 作業フォルダの FilePicker が使う commander の作業フォルダ API
  await page.route(/\/api\/v1\/commander\/workdir\/roots$/, (route) => route.fulfill({ json: [ROOT] }));
  await page.route(/\/api\/v1\/commander\/workdir\/list/, (route) => {
    const listed = new URL(route.request().url()).searchParams.get("path") ?? ROOT;
    return route.fulfill({
      json: {
        path: listed,
        entries: listed === ROOT ? [{ name: "projects", path: `${ROOT}/projects`, is_dir: true, hidden: false }] : [],
      },
    });
  });
  // FilePicker の「＋ 新しいフォルダ」
  await page.route(/\/api\/v1\/commander\/workdir\/directory$/, (route) => {
    state.folderPosts.push((route.request().postDataJSON() as { path: string }).path);
    return route.fulfill({ json: { ok: true } });
  });
  // 端末の WebSocket: 接続したら空の履歴で「Live」にする（実 CLI・実 tmux を使わない）
  await page.routeWebSocket(/\/terminals\/[0-9a-f]+\/connect/, (ws) => {
    ws.send(JSON.stringify({ type: "history_reset", connectionGeneration: 1 }));
    ws.send(JSON.stringify({
      type: "history_end", connectionGeneration: 1, sequence: 0,
      historyTotal: 0, historyPosition: 0,
    }));
    const timer = setInterval(() => {
      ws.send(JSON.stringify({ type: "heartbeat", connectionGeneration: 1 }));
    }, 5_000);
    ws.onClose(() => clearInterval(timer));
  });
}

test.describe("AI Commander", () => {
  test("モバイル 320px: 一覧から 1 個ずつ開き、閉じる・削除も確認できる", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 320, height: 700 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");
    await expect(page.getByRole("heading", { name: "Nexarch" })).toBeVisible();

    // 1 つずつのリスト。CLI 名・アカウント・表示名・最終活動が出る
    const rows = page.locator("[data-commander-row]");
    await expect(rows).toHaveCount(2);
    const first = rows.first();
    await expect(first).toContainText("Claude Code");
    await expect(first).toContainText("既定");
    await expect(first).toContainText("調査");
    await expect(first).toContainText("最終活動");
    // 5 時間・週の使用率と回復までの時間（80% 以上は注意の色）
    await expect(first.locator("[data-commander-usage]")).toContainText("5時間82%");
    await expect(first.locator("[data-commander-usage]")).toContainText("あと 2時間10分");
    await expect(first.locator("[data-commander-usage]")).toContainText("週40%");
    await expect(first.locator("[data-commander-usage]")).toContainText("あと 3日5時間");
    // C2: 状態（LLM なしの判定）と最後の行。上限で停止ならその行を出す
    await expect(first.locator("[data-commander-state]")).toHaveAttribute("data-commander-state", "working");
    await expect(first).toContainText("作業中");
    await expect(first).toContainText("Drizzling");
    const second = rows.nth(1);
    await expect(second).toContainText("上限で停止");
    await expect(second).toContainText("429 Too Many Requests");
    // C3: 自動再開の予定と、コンソールごとの止める・戻す（押しても全画面は開かない）
    await expect(second).toContainText("予定 22:21");
    await expect(second).not.toContainText("続けてください");
    const toggleSwitch = second.getByRole("switch", { name: "自動再開" });
    await expect(toggleSwitch).toHaveAttribute("aria-checked", "true");
    await toggleSwitch.click();
    await expect(toggleSwitch).toHaveAttribute("aria-checked", "false");
    await expect(second).not.toContainText("予定 22:21");
    await expect(page.locator("[data-terminal-root]")).toHaveCount(0);
    await toggleSwitch.click();
    await expect(toggleSwitch).toHaveAttribute("aria-checked", "true");
    expect(state.autoresumePuts).toEqual([{ id: "aa110002", enabled: false }, { id: "aa110002", enabled: true }]);
    // トグルは全コンソールに出る（上限で止まっていなくても先に切っておける）
    await expect(first.getByRole("switch", { name: "自動再開" })).toHaveAttribute("aria-checked", "true");

    // 再開の文言: 全体（その他メニュー）→ コンソールごと（✏️。空なら全体を使う）
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "自動再開の文言（全体）" }).click();
    const globalSheet = page.getByRole("dialog", { name: "自動再開の文言（全体）" });
    await globalSheet.getByLabel("全体の再開の文言").fill("上限が戻ったので続きをお願いします");
    await globalSheet.getByRole("button", { name: "保存" }).click();
    await expect(globalSheet).toHaveCount(0);
    expect(state.resumeMessage).toBe("上限が戻ったので続きをお願いします");
    await second.getByRole("button", { name: "再開の文言" }).click();
    const own = page.getByRole("dialog", { name: /の再開の文言/ });
    await expect(own).toContainText("上限が戻ったので続きをお願いします");
    await own.getByLabel("このコンソールの再開の文言").fill("go on");
    await own.getByRole("button", { name: "保存" }).click();
    await expect(own).toHaveCount(0);
    expect(state.consoleMessagePuts).toEqual([{ id: "aa110002", message: "go on" }]);
    await expect(page.locator("[data-terminal-root]")).toHaveCount(0);
    await expect(page.getByText("未導入")).toHaveCount(0);

    // 押すと全画面の端末。閉じると一覧へ戻る
    await first.getByRole("button", { name: "開く" }).click();
    await expect(page.locator("[data-terminal-root]")).toBeVisible();
    await expect.poll(async () => page.locator("[data-terminal-root]").getAttribute("data-terminal-replaying")).toBe("false");
    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await expect(page.locator("[data-terminal-root]")).toHaveCount(0);
    await expect(rows).toHaveCount(2);

    // 削除は確認ダイアログを挟む
    await first.getByRole("button", { name: "Claude Codeのコンソールを閉じる" }).click();
    const confirm = page.getByRole("alertdialog", { name: "Claude Code のコンソールを閉じますか？" });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "キャンセル" }).click();
    await expect(rows).toHaveCount(2);
    await first.getByRole("button", { name: "Claude Codeのコンソールを閉じる" }).click();
    await confirm.getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(rows).toHaveCount(1);
    expect(state.consoleDeletes).toEqual(["aa110001"]);

    // 横はみ出しが無い
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.screenshot({ path: "test-results/commander-320-light.png", fullPage: true });

    // ダークも撮る
    await page.evaluate(() => localStorage.setItem("cd-theme", "dark"));
    await page.reload();
    await expect(rows).toHaveCount(1);
    await page.screenshot({ path: "test-results/commander-320-dark.png", fullPage: true });

    // 表示の切り替え（文字の無いアイコンのボタン）: タイルにすると中身が見え、再読み込みでも覚えている
    const toggle = page.locator("[data-commander-view-toggle]");
    await expect(toggle).toHaveAttribute("data-view", "list");
    await expect(toggle).toHaveAccessibleName("中身を見ながら表示");
    expect((await toggle.innerText()).trim()).toBe("");
    // 見出しが切れない（切り替えのボタンを足しても 320px に収まる）
    expect(await page.getByRole("heading", { name: "Nexarch" }).evaluate((h) => h.scrollWidth <= h.clientWidth)).toBe(true);
    await toggle.click();
    const tiles = page.locator("[data-commander-tile]");
    await expect(tiles).toHaveCount(1);
    await expect(rows).toHaveCount(0);
    await expect.poll(async () => tiles.first().locator("[data-terminal-root]").getAttribute("data-terminal-replaying")).toBe("false");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.screenshot({ path: "test-results/commander-320-tiles-dark.png", fullPage: true });
    await page.reload();
    await expect(tiles).toHaveCount(1);
    await expect(toggle).toHaveAttribute("data-view", "tiles");
  });

  test("＋ コンソール: 未導入は選べない・アカウント・フォルダ・個数・表示名を選んで開ける（320px）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 320, height: 700 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    await page.getByRole("button", { name: "＋ コンソール" }).click();
    const sheet = page.getByRole("dialog", { name: "コンソールを開く" });
    await expect(sheet).toBeVisible();

    // 未導入の CLI は選べない（理由も出す）
    const gemini = sheet.getByRole("radio", { name: /Gemini CLI/ });
    await expect(gemini).toBeDisabled();
    await expect(gemini).toContainText("未導入（開けません）");

    // claude の既定アカウントは「ログイン済み」の印が出る
    await sheet.getByRole("radio", { name: /Claude Code/ }).click();
    await expect(sheet.getByText("ログイン済み", { exact: true })).toBeVisible();
    await expect(sheet.getByRole("radio", { name: /既定のアカウント/ })).toBeChecked();

    // OpenCode は ControlDeck の接続で開く。モデル（接続）の一覧は普段は出さず、Models で選ぶ案内の 1 行だけ
    await sheet.getByRole("radio", { name: /OpenCode/ }).click();
    await expect(sheet.locator("[data-opencode-console-note]")).toContainText("モデルは Models のフロンティアモデル");
    await expect(sheet.getByRole("radio", { name: /DeepSeek V4.1 Flash（openrouter）/ })).toBeHidden();
    await expect(sheet.getByRole("radio", { name: "＋ 新しいアカウント" })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    // codex の新しいアカウントをその場で作る（既定のアカウントは「既定」ではなくログインの名前で出す）
    await sheet.getByRole("radio", { name: /Codex CLI/ }).click();
    await expect(sheet.getByRole("radio", { name: /ChatGPT でログイン/ })).toBeVisible();
    await sheet.getByRole("radio", { name: "＋ 新しいアカウント" }).click();
    // 名前は入れない（内部の名前は自動・表示はログインしたメールアドレス。2026-11-28 利用者）
    await expect(sheet.getByLabel("新しいアカウント名")).toHaveCount(0);
    await expect(sheet.getByText("ログインしたメールアドレスが、アカウントの名前として出ます")).toBeVisible();

    // 作業フォルダは FilePicker（files API の許可ルート内）
    // 既定は「新しい作業場所（自動の名前）」。既存のフォルダは下の選択肢
    await expect(sheet.getByRole("radio", { name: "新しい作業場所（自動の名前）" })).toBeChecked();
    await sheet.getByRole("radio", { name: "既存のフォルダを選ぶ" }).click();
    await sheet.getByRole("button", { name: /フォルダを選ぶ|選ぶ/ }).first().click();
    const picker = page.getByRole("dialog", { name: "作業フォルダを選択" });
    await expect(picker).toBeVisible();
    // 新しいフォルダをその場で作り、その中へ入って選ぶ（同じ名前・「/」は断る）
    await picker.getByRole("button", { name: "＋ 新しいフォルダ" }).click();
    await picker.getByLabel("新しいフォルダの名前").fill("projects");
    await expect(picker.getByText("同じ名前があります")).toBeVisible();
    await expect(picker.getByRole("button", { name: "作成" })).toBeDisabled();
    await picker.getByLabel("新しいフォルダの名前").fill("a/b");
    await expect(picker.getByText("「/」などは使えません")).toBeVisible();
    await picker.getByLabel("新しいフォルダの名前").fill("night-batch");
    await picker.getByRole("button", { name: "作成" }).click();
    await expect(picker.getByText(`${ROOT}/night-batch`)).toBeVisible();
    expect(state.folderPosts).toEqual([`${ROOT}/night-batch`]);
    await page.screenshot({ path: "test-results/commander-new-folder-320.png" });
    await picker.getByRole("button", { name: "このフォルダを選択" }).click();
    await expect(picker).toHaveCount(0);
    // 選んだパスが作業フォルダのボタンへ入る
    await expect(sheet.getByRole("button", { name: /選ぶ/ })).toContainText(`${ROOT}/night-batch`);

    // 個数 2・表示名
    await sheet.getByLabel("同時に開く数").selectOption("2");
    await sheet.getByLabel("コンソールの表示名").fill("夜間バッチ");

    await sheet.getByRole("button", { name: "開く", exact: true }).click();
    await expect(sheet).toHaveCount(0);
    // アカウントを作ってからコンソールを開いている
    expect(state.accountPosts).toEqual([{ cli: "codex", name: "account-2" }]);
    expect(state.consolePosts).toEqual([{
      cli: "codex", account: "account-2", cwd: `${ROOT}/night-batch`, count: 2, label: "夜間バッチ",
    }]);
    await expect(page.locator("[data-commander-row]")).toHaveCount(4);
    await expect(page.getByText("2 個のコンソールを開きました")).toBeVisible();
  });

  for (const [width, height] of [[320, 700], [1280, 900]] as const) {
    test(`＋ コンソール: 「新しい作業場所（自動の名前）」が既定で、名前を入れて開ける（${width}px）`, async ({ page, context }) => {
      test.skip(!hasSession(), "ログイン情報がありません");
      await page.setViewportSize({ width, height });
      await establishSession(page, context);
      const state = initialState();
      await mockCommander(page, state);
      await page.goto("/commander");

      await page.getByRole("button", { name: "＋ コンソール" }).click();
      const sheet = page.getByRole("dialog", { name: "コンソールを開く" });
      await expect(sheet).toBeVisible();
      // 既定は新しい作業場所。フォルダを選ばなくても開ける。選ぶ欄はその下の選択肢
      await expect(sheet.getByRole("radio", { name: "新しい作業場所（自動の名前）" })).toBeChecked();
      await expect(sheet.getByRole("button", { name: "開く", exact: true })).toBeEnabled();
      await expect(sheet.getByRole("button", { name: /フォルダを選ぶ/ })).toHaveCount(0);
      // コンソールの表示名とは別の欄
      await expect(sheet.getByLabel("作業場所の名前")).toBeVisible();
      await expect(sheet.getByLabel("コンソールの表示名")).toBeVisible();
      await page.screenshot({ path: `test-results/commander-new-workspace-${width}.png` });

      // 既存のフォルダに切り替えると、選ぶまで開けない
      await sheet.getByRole("radio", { name: "既存のフォルダを選ぶ" }).click();
      await expect(sheet.getByRole("button", { name: "開く", exact: true })).toBeDisabled();
      await sheet.getByRole("radio", { name: "新しい作業場所（自動の名前）" }).click();

      await sheet.getByLabel("作業場所の名前").fill("夜の調査");
      await sheet.getByLabel("コンソールの表示名").fill("調査");
      await sheet.getByRole("button", { name: "開く", exact: true }).click();
      await expect(sheet).toHaveCount(0);
      expect(state.consolePosts).toEqual([{
        cli: "claude", account: "default", cwd: "", count: 1, label: "調査", new_workspace: { name: "夜の調査" },
      }]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    });
  }

  test("アカウントの管理: 一覧・作る・消す（確認ダイアログ）（320px）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 320, height: 700 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    // その他メニューから開く
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "アカウントの管理" }).click();
    const sheet = page.getByRole("dialog", { name: "アカウントの管理" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText(/ControlDeck はログインの情報（鍵・トークン）を読みません/)).toBeVisible();

    // CLI ごとの一覧。「ログイン済み」の印と gemini の案内
    const claudeSection = sheet.getByRole("region", { name: "Claude Codeのアカウント" });
    await expect(claudeSection.getByText("既定のアカウント（普段の設定）")).toBeVisible();
    await expect(claudeSection.getByText("ログイン済み", { exact: true })).toBeVisible();
    await expect(claudeSection.locator("[data-commander-usage]").first()).toContainText("5時間12%");
    // Claude の使用率を Nexarch の外の分も取る（アカウントの設定の statusline）。別の statusline があるアカウントは案内だけ
    const statuslineSwitch = claudeSection.getByRole("switch", { name: /使用率を取る/ });
    await expect(statuslineSwitch).toHaveCount(1);
    await statuslineSwitch.click();
    await expect(statuslineSwitch).toHaveAttribute("aria-checked", "true");
    expect(state.statuslinePuts).toEqual([{ name: "default", enabled: true }]);
    await expect(claudeSection).toContainText("別の statusline があるため");
    await expect(claudeSection.getByText("work")).toBeVisible();
    // まず対象のコンソールを選ぶ。選んだものの登録済みのアカウントだけが出る
    await expect(sheet.getByRole("radio", { name: "Claude Code" })).toBeChecked();
    await expect(sheet.getByRole("region", { name: "Gemini CLIのアカウント" })).toHaveCount(0);

    // 作る
    // 名前は入れずに足す（2026-11-28 利用者「メールアドレスを勝手にアカウント名にできるなら入力欄は不要」）
    await expect(claudeSection.getByLabel("Claude Codeの新しいアカウント名")).toHaveCount(0);
    await claudeSection.getByRole("button", { name: "＋ アカウントを足す" }).click();
    await expect(claudeSection.getByText("account-2")).toBeVisible();
    expect(state.accountPosts).toEqual([{ cli: "claude", name: "account-2" }]);

    // 消す（破壊的なので確認ダイアログ）
    await claudeSection.getByRole("button", { name: "アカウント work を削除" }).click();
    const confirm = page.getByRole("alertdialog", { name: "アカウント「work」を削除しますか？" });
    await expect(confirm).toContainText("ログインの情報も消える");
    await confirm.getByRole("button", { name: "キャンセル" }).click();
    await expect(claudeSection.getByText("work")).toBeVisible();
    await claudeSection.getByRole("button", { name: "アカウント work を削除" }).click();
    await confirm.getByRole("button", { name: "削除する" }).click();
    await expect(claudeSection.getByText("work")).toHaveCount(0);
    expect(state.accountDeletes).toEqual(["claude/work"]);

    // 既定のアカウントにも名前を付けられる（✏️ → 入力 → 保存）
    await claudeSection.getByRole("button", { name: "既定 のアカウントに名前を付ける" }).click();
    await claudeSection.getByLabel("既定 のアカウントの名前").fill("仕事用");
    await claudeSection.getByRole("button", { name: "保存" }).click();
    await expect(claudeSection.getByText("仕事用")).toBeVisible();
    expect(state.labelPuts).toEqual([{ cli: "claude", name: "default", label: "仕事用" }]);
    // OpenCode は、モデル（接続）の一覧を普段は出さず、Models への案内 1 行だけ。接続の行はエキスパートで開く
    await sheet.getByRole("radio", { name: "OpenCode" }).click();
    const opencodeSection = sheet.getByRole("region", { name: "OpenCodeのアカウント" });
    await expect(opencodeSection).toContainText("OpenCode は ControlDeck の接続で動きます。モデルは Models のフロンティアモデル");
    await expect(opencodeSection.getByRole("link", { name: "Models で選ぶ" })).toHaveAttribute("href", /\/models\?tab=frontier/);
    await expect(opencodeSection).not.toContainText("DeepSeek V4.1 Flash（openrouter）");
    await expect(opencodeSection.locator("[data-opencode-connections]")).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.evaluate(() => localStorage.setItem("control-deck:commander-expert", "1"));
    await page.reload();
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "アカウントの管理" }).click();
    await page.getByRole("dialog", { name: "アカウントの管理" }).getByRole("radio", { name: "OpenCode" }).click();
    const expertSection = page.getByRole("dialog", { name: "アカウントの管理" }).getByRole("region", { name: "OpenCodeのアカウント" });
    await expect(expertSection).toContainText("DeepSeek V4.1 Flash（openrouter）");   // 長い名前は 1 行で省略（はみ出さない）
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await expect(sheet.getByRole("region", { name: "Claude Codeのアカウント" })).toHaveCount(0);
    await sheet.getByRole("radio", { name: "Gemini CLI" }).click();
    await expect(sheet.getByRole("region", { name: "Gemini CLIのアカウント" })).toContainText("既定のアカウントだけ使えます");
    await expect(sheet.getByRole("radio", { name: "Gemini CLI" })).toBeChecked();
    await expect(sheet.getByRole("radio", { name: "OpenCode" })).not.toBeChecked();
    await expect(sheet.getByRole("radio", { name: "Claude Code" })).not.toBeChecked();
    await page.waitForTimeout(400);
    await page.screenshot({ path: "test-results/commander-accounts-320.png", fullPage: true });
  });

  test("PC 1280px: タイル 2 列で並べ、押すと大きく開く（ライト・ダークの撮影も）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1280, height: 900 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    const tiles = page.locator("[data-commander-tile]");
    await expect(tiles).toHaveCount(2);
    const first = tiles.first();
    await expect(first).toContainText("Claude Code");
    await expect(first).toContainText("調査");
    // タイルの中に端末が埋め込まれている（Live になるまで待つ）
    await expect.poll(async () => first.locator("[data-terminal-root]").getAttribute("data-terminal-replaying")).toBe("false");

    // 2 列であること（横に並ぶ）
    const boxes = await tiles.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().x));
    expect(boxes[1]).toBeGreaterThan(boxes[0] + 200);

    // 押すと大きく開く
    await first.getByRole("button", { name: "大きく開く" }).first().click();
    await expect(page.locator("[data-terminal-root]")).toBeVisible();
    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await expect(page.locator("[data-commander-tile]")).toHaveCount(2);

    // 横はみ出しが無い
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.screenshot({ path: "test-results/commander-1280-light.png", fullPage: true });
    await page.evaluate(() => localStorage.setItem("cd-theme", "dark"));
    await page.reload();
    await expect(tiles).toHaveCount(2);
    await expect.poll(async () => first.locator("[data-terminal-root]").getAttribute("data-terminal-replaying")).toBe("false");
    await page.screenshot({ path: "test-results/commander-1280-dark.png", fullPage: true });

    // PC でも一覧へ切り替えられる（端末は埋め込まない）
    await expect(page.locator("[data-commander-view-toggle]")).toHaveAccessibleName("一覧で表示");
    await page.locator("[data-commander-view-toggle]").click();
    await expect(page.locator("[data-commander-row]")).toHaveCount(2);
    await expect(tiles).toHaveCount(0);
    await expect(page.locator("[data-terminal-root]")).toHaveCount(0);
    await page.screenshot({ path: "test-results/commander-1280-list-dark.png", fullPage: true });
  });

  test("ロック: 鍵は押すたび切り替わり、ロック中は閉じられない。一括はロック中を飛ばす（320px）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 320, height: 700 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    const rows = page.locator("[data-commander-row]");
    const first = rows.first();
    await expect(rows).toHaveCount(2);

    // 既定はロックなし。押すとロックし、もう一度押すと外れる（読み上げ名で状態が分かる）
    const firstLockOff = first.getByRole("button", { name: "Claude Codeのロックなし（押すとロックする）" });
    await expect(firstLockOff).toHaveAttribute("aria-pressed", "false");
    await firstLockOff.click();
    const firstLockOn = first.getByRole("button", { name: "Claude Codeのロック中（押すとロックを外す）" });
    await expect(firstLockOn).toHaveAttribute("aria-pressed", "true");
    await expect(first.locator("[data-commander-lock]")).toHaveAttribute("data-commander-lock", "on");
    expect(state.lockPuts).toEqual([{ id: "aa110001", locked: true }]);
    // ロック中は閉じるを出さず、「ロック中」の札を出す（2026-11-28 Claude のデザインの見直し）
    await expect(first.getByRole("button", { name: /Claude Codeのコンソールを閉じる/ })).toHaveCount(0);
    await expect(first.locator("[data-commander-locked-badge]")).toHaveText("🔒 ロック中");
    await firstLockOn.click();
    await expect(first.getByRole("button", { name: "Claude Codeのロックなし（押すとロックする）" })).toHaveAttribute("aria-pressed", "false");
    await expect(first.getByRole("button", { name: "Claude Codeのコンソールを閉じる" })).toBeEnabled();

    // 2 個目をロックして一括で閉じる。ロック中は残し、飛ばした数を知らせる
    await rows.nth(1).getByRole("button", { name: "OpenCodeのロックなし（押すとロックする）" }).click();
    await expect(rows.nth(1).getByRole("button", { name: /OpenCodeのコンソールを閉じる/ })).toHaveCount(0);
    await page.screenshot({ path: "test-results/commander-lock-320.png", fullPage: true });
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "すべてのコンソールを閉じる" }).click();
    const bulk = page.getByRole("alertdialog", { name: "すべてのコンソールを閉じますか？" });
    await expect(bulk).toContainText("ロック中の 1 個は閉じずに残します");
    await bulk.getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(page.getByText(/1 個のコンソールを閉じました/)).toBeVisible();
    await expect(page.getByText(/ロック中の 1 個は閉じていません/)).toBeVisible();
    await expect(rows).toHaveCount(1);
    expect(state.bulkDeletes).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    // ダーク・エキスパート入でも撮る（ロック中の 1 個が残っている。設定の項目がメニューに出る）
    await page.evaluate(() => {
      localStorage.setItem("cd-theme", "dark");
      localStorage.setItem("control-deck:commander-expert", "1");
    });
    await page.reload();
    await expect(rows).toHaveCount(1);
    await page.getByRole("button", { name: "その他の操作" }).click();
    await expect(page.getByRole("menuitem", { name: "自動で戻す（全体）" })).toBeVisible();
    await page.screenshot({ path: "test-results/commander-lock-320-dark-expert.png", fullPage: true });
    await page.keyboard.press("Escape");

    // ロックを外せば閉じられる
    const remaining = rows.first();
    await remaining.getByRole("button", { name: "OpenCodeのロック中（押すとロックを外す）" }).click();
    await remaining.getByRole("button", { name: "OpenCodeのコンソールを閉じる" }).click();
    await page.getByRole("alertdialog", { name: "OpenCode のコンソールを閉じますか？" }).getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(rows).toHaveCount(0);
  });

  test("ロック: 画面が古くロック中だったとき（409）は日本語で知らせる（320px）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 320, height: 700 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    const rows = page.locator("[data-commander-row]");
    await expect(rows).toHaveCount(2);
    // 画面はロックなしのまま、サーバー側だけロック中になった場合
    state.forceConflict = true;
    await rows.first().getByRole("button", { name: "Claude Codeのコンソールを閉じる" }).click();
    await page.getByRole("alertdialog", { name: "Claude Code のコンソールを閉じますか？" })
      .getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(page.getByText("ロック中です。先にロックを外してください")).toBeVisible();
    await expect(rows).toHaveCount(2);
    expect(state.consoleDeletes).toEqual([]);
  });

  test("自動で戻す（全体）: エキスパートのときだけ出て、切った設定を覚える（1280px・ライト/ダーク）", async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width: 1280, height: 900 });
    await establishSession(page, context);
    const state = initialState();
    await mockCommander(page, state);
    await page.goto("/commander");

    // 既定（エキスパート切）ではメニューに出さない
    await page.getByRole("button", { name: "その他の操作" }).click();
    await expect(page.getByRole("menuitem", { name: "自動で戻す（全体）" })).toHaveCount(0);
    await page.screenshot({ path: "test-results/commander-1280-expert-off.png" });
    await page.keyboard.press("Escape");

    // エキスパートを入れると出る。全体の設定なので、ロックしたコンソールが外から閉じられたら開き直す（既定は入）
    await page.evaluate(() => localStorage.setItem("control-deck:commander-expert", "1"));
    await page.reload();
    await expect(page.locator("[data-commander-tile]")).toHaveCount(2);
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "自動で戻す（全体）" }).click();
    const sheet = page.getByRole("dialog", { name: "自動で戻す（全体）" });
    const toggle = sheet.getByRole("switch", { name: "自動で戻す" });
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(sheet.locator("[data-auto-restore-state]")).toHaveAttribute("data-auto-restore-state", "off");
    expect(state.autoRestore).toBe(false);
    await page.screenshot({ path: "test-results/commander-autorestore-1280.png" });

    // 再読み込みしても覚えている（全体の設定）
    await page.reload();
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "自動で戻す（全体）" }).click();
    await expect(page.getByRole("switch", { name: "自動で戻す" })).toHaveAttribute("aria-checked", "false");

    // ダークでも撮る
    await page.evaluate(() => localStorage.setItem("cd-theme", "dark"));
    await page.reload();
    await page.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "自動で戻す（全体）" }).click();
    await expect(page.getByRole("dialog", { name: "自動で戻す（全体）" })).toBeVisible();
    await page.screenshot({ path: "test-results/commander-autorestore-1280-dark.png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  });
});

// 目的: 設定 → オプション機能に「AI の CLI（Nexarch）」の分類があり、Claude Code・Codex・Gemini を導入・更新・削除できる。
// 根拠: 2026-11-27 利用者「各 CLI の導入と削除と更新ができるように Settings の機能に追加して」。押すと npm で取得するので押さない。
test("設定: AI の CLI（Nexarch）の分類に 3 つの CLI が出る（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 320, height: 900 });
  await establishSession(page, context);
  await page.goto("/settings/features");
  await expect(page.getByText("AI の CLI（Nexarch）", { exact: false }).first()).toBeVisible();
  for (const id of ["claude-code", "codex-cli", "gemini-cli"]) {
    await expect(page.locator(`#feature-${id}`)).toBeVisible();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  await page.locator("#feature-claude-code").screenshot({ path: "test-results/settings-cli-claude-320.png" });
});
