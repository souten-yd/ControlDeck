/** AI Commander（/commander）の E2E。API と端末の WebSocket はモック（実 CLI を動かさない）。
 *
 * 目的（c1-spec.md「E2E」の項目）:
 * - ＋ コンソールのシートの流れ（未導入の CLI は選べない・アカウント・作業フォルダ・個数・表示名）
 * - タイル（1280px）とモバイル 320px の 1 つずつ（リスト → 全画面）
 * - アカウントを作る・消す（確認ダイアログ）
 * - 320 と 1280 のライト・ダークの撮影
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
}

function initialState(): CommanderState {
  return {
    consoles: [
      consoleFixture({
        id: "aa110001", program: "claude", commander_cli: "claude",
        commander_account: "default", commander_label: "調査",
        commander_state: "working", commander_last_line: "· Drizzling… (2m 39s · ↓ 16k tokens)",
      }),
      consoleFixture({
        id: "aa110002", program: "opencode", cwd: "~/CodeDEV/Hello",
        commander_cli: "opencode", commander_account: "default", commander_account_label: "ControlDeck の API",
        commander_state: "limited", commander_last_line: "Build · DeepSeek",
        commander_detail: "Error: 429 Too Many Requests — rate limit exceeded",
      }),
    ],
    accounts: {
      claude: [
        { name: "default", default: true, logged_in: true, label: "" },
        { name: "work", default: false, logged_in: false, label: "" },
      ],
      codex: [{ name: "default", default: true, logged_in: null, label: "" }],
      opencode: [
        { name: "default", default: true, logged_in: null, label: "ControlDeck の API", title: "OpenCode の設定のモデル（auto・ゲートウェイ）" },
        { name: "api-1a2b3c4d5e", default: false, logged_in: null, label: "", title: "DeepSeek V4.1 Flash（openrouter）" },
      ],
      gemini: [{ name: "default", default: true, logged_in: null, label: "" }],
    },
    consolePosts: [],
    consoleDeletes: [],
    accountPosts: [],
    accountDeletes: [],
    labelPuts: [],
    folderPosts: [],
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
    const consoleMatch = path.match(/^consoles\/([0-9a-f]+)$/);
    if (consoleMatch && method === "DELETE") {
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
  // 作業フォルダの FilePicker が使う files API
  await page.route(/\/api\/v1\/files\/roots$/, (route) => route.fulfill({ json: [ROOT] }));
  await page.route(/\/api\/v1\/files\/list/, (route) => {
    const listed = new URL(route.request().url()).searchParams.get("path") ?? ROOT;
    return route.fulfill({
      json: {
        path: listed,
        entries: listed === ROOT ? [{ name: "projects", path: `${ROOT}/projects`, is_dir: true, hidden: false }] : [],
      },
    });
  });
  // FilePicker の「＋ 新しいフォルダ」
  await page.route(/\/api\/v1\/files\/directory$/, (route) => {
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
    await expect(page.getByRole("heading", { name: "AI Commander" })).toBeVisible();

    // 1 つずつのリスト。CLI 名・アカウント・表示名・最終活動が出る
    const rows = page.locator("[data-commander-row]");
    await expect(rows).toHaveCount(2);
    const first = rows.first();
    await expect(first).toContainText("Claude Code");
    await expect(first).toContainText("既定");
    await expect(first).toContainText("調査");
    await expect(first).toContainText("最終活動");
    // C2: 状態（LLM なしの判定）と最後の行。上限で停止ならその行を出す
    await expect(first.locator("[data-commander-state]")).toHaveAttribute("data-commander-state", "working");
    await expect(first).toContainText("作業中");
    await expect(first).toContainText("Drizzling");
    const second = rows.nth(1);
    await expect(second).toContainText("上限で停止");
    await expect(second).toContainText("429 Too Many Requests");
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
    expect(await page.getByRole("heading", { name: "AI Commander" }).evaluate((h) => h.scrollWidth <= h.clientWidth)).toBe(true);
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

    // OpenCode は ControlDeck の API で開く（アカウントは既定の 1 つ。付けた名前が出る）
    await sheet.getByRole("radio", { name: /OpenCode/ }).click();
    await expect(sheet.getByRole("radio", { name: /ControlDeck の API/ })).toBeChecked();
    await expect(sheet.getByRole("radio", { name: /DeepSeek V4.1 Flash（openrouter）/ })).toBeVisible();
    await expect(sheet.getByText(/選んだ接続（OpenCode 画面に登録した API）で開きます/)).toBeVisible();
    await expect(sheet.getByRole("radio", { name: "＋ 新しいアカウント" })).toHaveCount(0);

    // codex の新しいアカウントをその場で作る
    await sheet.getByRole("radio", { name: /Codex CLI/ }).click();
    await sheet.getByRole("radio", { name: "＋ 新しいアカウント" }).click();
    await sheet.getByLabel("新しいアカウント名").fill("sub2");

    // 作業フォルダは FilePicker（files API の許可ルート内）
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
    await sheet.getByLabel("開く個数").selectOption("2");
    await sheet.getByLabel("コンソールの表示名").fill("夜間バッチ");

    await sheet.getByRole("button", { name: "開く", exact: true }).click();
    await expect(sheet).toHaveCount(0);
    // アカウントを作ってからコンソールを開いている
    expect(state.accountPosts).toEqual([{ cli: "codex", name: "sub2" }]);
    expect(state.consolePosts).toEqual([{
      cli: "codex", account: "sub2", cwd: `${ROOT}/night-batch`, count: 2, label: "夜間バッチ",
    }]);
    await expect(page.locator("[data-commander-row]")).toHaveCount(4);
    await expect(page.getByText("2 個のコンソールを開きました")).toBeVisible();
  });

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
    await expect(claudeSection.getByText("work")).toBeVisible();
    // まず対象のコンソールを選ぶ。選んだものの登録済みのアカウントだけが出る
    await expect(sheet.getByRole("radio", { name: "Claude Code" })).toBeChecked();
    await expect(sheet.getByRole("region", { name: "Gemini CLIのアカウント" })).toHaveCount(0);

    // 作る
    await claudeSection.getByLabel("Claude Codeの新しいアカウント名").fill("sub");
    await claudeSection.getByRole("button", { name: "追加" }).click();
    await expect(claudeSection.getByText("sub")).toBeVisible();
    expect(state.accountPosts).toEqual([{ cli: "claude", name: "sub" }]);

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
    // OpenCode を選ぶと、OpenCode 画面に登録した接続がアカウントとして並ぶ
    await sheet.getByRole("radio", { name: "OpenCode" }).click();
    const opencodeSection = sheet.getByRole("region", { name: "OpenCodeのアカウント" });
    await expect(opencodeSection).toContainText("ControlDeck の API");
    await expect(opencodeSection).toContainText("DeepSeek V4.1 Flash（openrouter）");
    await expect(opencodeSection).toContainText("OpenCode 画面に登録した接続");
    await expect(opencodeSection.getByLabel("OpenCodeの新しいアカウント名")).toHaveCount(0);
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
});
