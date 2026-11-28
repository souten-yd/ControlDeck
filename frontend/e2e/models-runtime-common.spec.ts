import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Models の共通ランタイム部品（目録・ファイル登録・設定）の受け入れ E2E。
 *
 *  目的（Nexarch #14 / 2026-11-28）:
 *   - llama.cpp / Strata / Radiance（Sly 派生を含む）/ Maya が、登録シートで
 *     同じ順番・同じ見出しの共通 3 部品（目録 → ファイル登録 → 設定）を出すこと。
 *   - 設定の节が ①モデル ②コンテキストの長さ ③同時に処理する数 ④速さの調整 ⑤起動 の順で、
 *     ⑥エキスパートはエキスパート入のときだけ出ること。
 *   - 取得ジョブの「停止」→「再開」が API のモックで動くこと（.part を残す/再開の意味は
 *     docs/PLAN-model-catalog.md「決めた意味」を照合）。
 *   - ファイル登録の FilePicker のルートが /data1tb と /data2tb だけであること
 *     （RuntimeRegisterFromFile.MODEL_FILE_PICKER_SOURCE と defense in depth）。
 *   - 320/1280px × ライト/ダーク × エキスパート切/入の画面写真を残すこと。
 *
 *  根拠: #11（Radiance・Maya）/#12（Strata・llama.cpp）で共通部品へ寄せた後の確認。
 *  API は page.route で差し替え、本物の設定・外部への取得は一切行わない。
 *
 *  ※ この環境では frontend/node_modules が無く実行不能。spec と実行コマンドのみを残し、
 *    結果は「未検証」とする。実行コマンド:
 *      env -u CONTROL_DECK_CONFIG \
 *        PLAYWRIGHT_BROWSERS_PATH=/data1tb/ControlDeck/data/cache/ms-playwright \
 *        ./deck.sh e2e models-runtime-common.spec.ts
 */
const RUNTIME_BUTTONS = ["llama.cpp", "Strata", "Radiance", "Maya"] as const;
const SECTION_LABELS = ["モデル", "コンテキストの長さ", "同時に処理する数", "速さの調整", "起動"] as const;
const RUNTIME_IDS = ["llama", "strata", "sly-radiance", "radiance", "maya"] as const;
const FILE_PICKER_TITLE = "モデルのファイルを選択（/data1tb・/data2tb）";

const catalogEntry = (id: string, acquire: "hf" | "none") => ({
  id,
  name: `E2E ${id}`,
  kind: "model",
  format: "gguf",
  repo: `e2e/${id}`,
  quantization: "Q4_K_M",
  size_gib: 1.5,
  memory_gib: 2,
  uncensored: false,
  supported: true,
  note: "",
  acquire,
  source: "e2e",
  fit: "fits",
});

const catalog = {
  runtimes: RUNTIME_IDS.map((id) => ({
    id,
    name: id,
    installed: true,
    formats: [],
    acquire: id === "radiance" ? "none" : "hf",
    entries: [catalogEntry(`e2e-${id}`, id === "radiance" ? "none" : "hf")],
    catalog_ref: `/api/v1/models/catalog/${id}`,
  })),
};

const field = (id: string, label: string, type: string, expert = false, fallback: unknown = "") => ({
  id, label, type, default: fallback, expert,
});
const capabilities = {
  runtimes: Object.fromEntries(RUNTIME_IDS.map((id) => [id, {
    sections: [
      { id: "model", label: "モデル", fields: [field("alias", "モデル名", "text")] },
      { id: "context", label: "コンテキストの長さ", fields: [field("ctx_size", "コンテキストの長さ", "int", false, 4096)] },
      { id: "parallel", label: "同時に処理する数", fields: [field("n_parallel", "同時に処理する数", "int", false, 1)] },
      { id: "speed", label: "速さの調整", fields: [field("spec", "投機的デコード", "bool", false, false)] },
      { id: "launch", label: "起動", fields: [field("on_demand_start", "呼ばれたら起動", "bool", false, true)] },
      { id: "expert", label: "エキスパート", fields: [field("internal", "内部の値", "text", true)] },
    ],
    always_on: [],
    endpoints: { instances: `/api/v1/models/${id}/instances` },
  }])),
};

interface JobMocks {
  calls: string[];
}

/** Models 画面と登録シートが参照する API をまとめて差し替える。取得ジョブの状態もここで持つ。 */
async function mockRuntimeCommon(page: Page): Promise<JobMocks> {
  await page.route("**/api/v1/models/runtime-environment", (route) => route.fulfill({ json: {
    platform: "linux", gpu: "e2e",
    runtimes: RUNTIME_IDS.map((id) => ({ id, runtime: id, label: id, installed: true, available: true })),
    policy: {
      selected_runtime: "llama.cpp", selected_backend: "", coexistence: "exclusive",
      idle_unload_enabled: false, idle_unload_minutes: 30, max_loaded_models: 1, assistant_name: "Assistant",
    },
  } }));
  await page.route("**/api/v1/models/llama/status", (route) => route.fulfill({ json: { installed: true, instances: [] } }));
  await page.route("**/api/v1/models/strata/instances", (route) => route.fulfill({ json: { runtime: { installed: true }, instances: [] } }));
  await page.route("**/api/v1/models/radiance/instances", (route) => route.fulfill({ json: {
    runtime: { installed: true, version: "fixture", warning: "" }, instances: [],
  } }));
  await page.route("**/api/v1/models/sly-radiance/instances", (route) => route.fulfill({ json: {
    runtime: { installed: true, version: "fixture" }, instances: [],
  } }));
  await page.route("**/api/v1/features/maya-s/model", (route) => route.fulfill({ json: { installed: true } }));
  await page.route("**/api/v1/features/maya-s/catalog*", (route) => route.fulfill({ json: {
    folder: "", memory: { ram_gb: 64, vram_gb: 24 }, quants: [],
  } }));
  await page.route("**/api/v1/models/instances", (route) => route.fulfill({ json: { instances: [] } }));
  await page.route("**/api/v1/models/providers/*/models", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/models/catalog", (route) => route.fulfill({ json: catalog }));
  await page.route("**/api/v1/models/settings/capabilities", (route) => route.fulfill({ json: capabilities }));
  await page.route("**/api/v1/commander/workdir/roots", (route) => route.fulfill({ json: ["/data1tb", "/data2tb"] }));
  await page.route("**/api/v1/files/list", (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "/data1tb";
    return route.fulfill({ json: { path, entries: [
      { name: "model.gguf", path: `${path}/model.gguf`, is_dir: false, hidden: false },
      // 許可ルート外の値が返ってきても onSelect で弾く（defense in depth）ことを確かめる。
      { name: "evil.gguf", path: "/tmp/evil.gguf", is_dir: false, hidden: false },
    ] } });
  });
  await page.route("**/api/v1/models/register/inspect*", (route) => route.fulfill({ json: {
    path: new URL(route.request().url()).searchParams.get("path") ?? "/data1tb/model.gguf",
    kind: "file", format: "gguf", alias_suggestion: "model", size_bytes: 1_500_000_000,
    metadata: { quantization: "Q4_K_M" },
    runtimes: RUNTIME_IDS.map((id) => ({ runtime: id, eligible: true, reason: "", register_endpoint: `/api/v1/models/${id}/instances` })),
  } }));

  // 取得ジョブ: job-1 を停止すると interrupted（can_resume=true）になり、再開で job-2 が走る。
  const calls: string[] = [];
  let running = true;
  await page.route("**/api/v1/models/catalog/llama/entries/e2e-llama/files", (route) => route.fulfill({ json: {
    repo: "e2e/e2e-llama",
    files: [{ path: "e2e-llama-Q4_K_M.gguf", size: 1_500_000_000 }],
    total_bytes: 1_500_000_000, libraries: [],
  } }));
  await page.route("**/api/v1/models/acquire-jobs", (route) => {
    calls.push("acquire");
    return route.fulfill({ status: 201, json: { job_id: "job-1", repo: "e2e/e2e-llama" } });
  });
  await page.route("**/api/v1/jobs/job-1", (route) => route.fulfill({ json: {
    id: "job-1", kind: "model.hf_download", title: "E2E e2e-llama",
    status: running ? "running" : "interrupted",
    progress: { status: running ? "取得中" : "停止中（続きから再開できます）", completed: 4, total: 10 },
    error: "", can_resume: !running,
  } }));
  await page.route("**/api/v1/jobs/job-1/stop", (route) => {
    calls.push("stop"); running = false;
    return route.fulfill({ json: { ok: true, status: "interrupted", can_resume: true } });
  });
  await page.route("**/api/v1/jobs/job-1/resume", (route) => {
    calls.push("resume");
    return route.fulfill({ json: { ok: true, job_id: "job-2" } });
  });
  await page.route("**/api/v1/jobs/job-2", (route) => route.fulfill({ json: {
    id: "job-2", kind: "model.hf_download", title: "E2E e2e-llama", status: "running",
    progress: { status: "取得中", completed: 1, total: 10 }, error: "", can_resume: false,
  } }));
  return { calls };
}

async function openRegisterSheet(page: Page, context: BrowserContext): Promise<{ sheet: ReturnType<Page["getByRole"]>; jobs: JobMocks }> {
  test.skip(!hasSession(), "E2E 資格情報がありません");
  await establishSession(page, context);
  const jobs = await mockRuntimeCommon(page);
  await page.goto("/models");
  await page.getByRole("button", { name: "モデル登録", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "モデル登録" });
  await expect(sheet).toBeVisible();
  return { sheet, jobs };
}

test("モデル登録: 4 ランタイムが同じ順番・同じ見出しで目録 → ファイル登録 → 設定を出す", async ({ page, context }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const { sheet } = await openRegisterSheet(page, context);

  const buttons = sheet.getByRole("button", { name: /^(llama\.cpp|Strata|Radiance|Maya)$/ });
  await expect(buttons).toHaveText([...RUNTIME_BUTTONS]);

  // llama.cpp / Strata は共通部品を SegmentedTabs で切り替える。目録 → ファイルの順。
  await expect(sheet.getByRole("button", { name: "目録から取得" })).toHaveAttribute("aria-pressed", "true");
  await expect(sheet.locator('[data-runtime-catalog="llama"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-entry="e2e-llama"]')).toContainText("E2E e2e-llama");
  await expect(sheet.locator('[data-runtime-catalog="llama"]')).toHaveAttribute("data-expert", "off");
  await sheet.getByRole("button", { name: "ファイルから登録", exact: true }).click();
  await expect(sheet.locator('[data-runtime-register-file="llama"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-register-file="llama"]')).toHaveAttribute("data-expert", "off");
  await expect(sheet.locator('[data-runtime-register-file="llama"]').getByRole("button", { name: "モデルのファイルを選ぶ" })).toBeVisible();

  // ランタイムを切り替えてもタブの状態が残ることがあるので、毎回 目録 を選び直す。
  await sheet.getByRole("button", { name: "Strata", exact: true }).click();
  await sheet.getByRole("button", { name: "目録から取得" }).click();
  await expect(sheet.locator('[data-runtime-catalog="strata"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-entry="e2e-strata"]')).toContainText("E2E e2e-strata");
  await sheet.getByRole("button", { name: "ファイルから登録", exact: true }).click();
  await expect(sheet.locator('[data-runtime-register-file="strata"]')).toBeVisible();

  // Radiance は Sly 派生が既定。目録 → ファイル → 設定がこの順で並ぶ。
  // 目録・ファイルの見出しは閉じた <details> の <summary> なので、開いてから中身を見る。
  await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([
    /目録から取る/, /ファイルから登録/, /モデル/, /コンテキストの長さ/, /同時に処理する数/, /速さの調整/, /起動/,
  ]);
  await sheet.locator("summary").filter({ hasText: /^目録から取る/ }).click();
  await expect(sheet.locator('[data-runtime-catalog="sly-radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-entry="e2e-sly-radiance"]')).toContainText("E2E e2e-sly-radiance");
  await sheet.locator("summary").filter({ hasText: /^ファイルから登録/ }).click();
  await expect(sheet.locator('[data-runtime-register-file="sly-radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="sly-radiance"]')).toBeVisible();
  // スイッチを切ると元の Radiance の見出しになる（共通部品はそのまま）。
  await sheet.getByRole("switch", { name: "Sly-Radiance で登録" }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([
    /目録から探す/, /ファイルから登録/, /モデル/, /コンテキストの長さ/, /同時に処理する数/, /速さの調整/, /起動/,
  ]);
  await sheet.locator("summary").filter({ hasText: /^目録から探す/ }).click();
  await expect(sheet.locator('[data-runtime-catalog="radiance"]')).toBeVisible();
  await sheet.locator("summary").filter({ hasText: /^ファイルから登録/ }).click();
  await expect(sheet.locator('[data-runtime-register-file="radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="radiance"]')).toBeVisible();

  // Maya は目録 → ファイル → Maya の目録 → 設定。
  await sheet.getByRole("button", { name: "Maya", exact: true }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([
    /目録から取る/, /ファイルから登録/, /Maya の目録/,
    /モデル/, /コンテキストの長さ/, /同時に処理する数/, /速さの調整/, /起動/,
  ]);
  await sheet.locator("summary").filter({ hasText: /^目録から取る/ }).click();
  await expect(sheet.locator('[data-runtime-catalog="maya"]')).toBeVisible();
  await sheet.locator("summary").filter({ hasText: /^ファイルから登録/ }).click();
  await expect(sheet.locator('[data-runtime-register-file="maya"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="maya"]')).toBeVisible();
});

for (const expert of [false, true]) {
  test(`共通設定: 見出しの並びがランタイム間で同じで、エキスパートは${expert ? "入" : "切"}のときだけ出る`, async ({ page, context }) => {
    await page.addInitScript((on) => {
      try { localStorage.setItem("control-deck:commander-expert", on ? "1" : "0"); } catch { /* 無視 */ }
    }, expert);
    await page.setViewportSize({ width: 1280, height: 900 });
    const { sheet } = await openRegisterSheet(page, context);

    // シートは llama.cpp で開くので、Sly-Radiance（Radiance ボタン中のスイッチ）と
    // Maya は対応するランタイムボタンを押してから共通設定を見る。
    for (const runtime of ["sly-radiance", "maya"] as const) {
      if (runtime === "sly-radiance") await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
      else await sheet.getByRole("button", { name: "Maya", exact: true }).click();
      const settings = sheet.locator(`[data-runtime-settings="${runtime}"]`);
      await expect(settings).toHaveAttribute("data-expert", expert ? "on" : "off");
      // modelSlot の中にある SettingsGroup を混ぜないよう、共通設定の直下だけを見る。
      await expect(settings.locator(":scope > section > h3")).toHaveText([...SECTION_LABELS]);
      const expertSection = settings.locator(":scope > details > summary", { hasText: "エキスパート" });
      if (expert) await expect(expertSection).toBeVisible();
      else await expect(expertSection).toHaveCount(0);
    }
  });
}

test("取得ジョブ: 停止 → 再開のボタンがモックの API で動く（320px）", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const { sheet, jobs } = await openRegisterSheet(page, context);

  const entry = sheet.locator('[data-catalog-entry="e2e-llama"]');
  await entry.getByRole("button", { name: "取得する" }).click();
  // 確認パネルは目録の <ul> より前に描かれる。先に出る「取得する」が確認パネルのボタン。
  await sheet.getByRole("button", { name: "取得する" }).first().click();

  const running = sheet.locator('[data-acquire-job="job-1"]');
  await expect(running).toContainText("取得中");
  await expect(running.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
  await expect(running.getByRole("button", { name: "再開", exact: true })).toHaveCount(0);
  await expect(running.getByText("サーバー側で実行中 — 画面を閉じても継続します")).toBeVisible();

  await running.getByRole("button", { name: "停止", exact: true }).click();
  // トーストは document.body へ portal されるので sheet 配下には無い。
  await expect(page.getByText("停止しました（続きから再開できます）")).toBeVisible();
  await expect(running).toContainText("停止中（続きから再開できます）");
  await expect(running.getByRole("button", { name: "再開", exact: true })).toBeEnabled();
  await expect(running.getByRole("button", { name: "停止", exact: true })).toHaveCount(0);
  expect(jobs.calls).toContain("stop");

  await running.getByRole("button", { name: "再開", exact: true }).click();
  const resumed = sheet.locator('[data-acquire-job="job-2"]');
  await expect(resumed).toContainText("取得中");
  await expect(resumed.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
  expect(jobs.calls).toContain("resume");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("ファイル登録の FilePicker: ルートは /data1tb と /data2tb だけ", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const { sheet } = await openRegisterSheet(page, context);
  await sheet.getByRole("button", { name: "ファイルから登録", exact: true }).click();
  await sheet.getByRole("button", { name: "モデルのファイルを選ぶ" }).click();

  const picker = page.getByRole("dialog", { name: FILE_PICKER_TITLE });
  await expect(picker).toBeVisible();
  await expect(picker.locator("ul > li > button")).toHaveText(["/data1tb", "/data2tb"]);
  await expect(picker).not.toContainText("/home");

  await picker.locator("ul > li > button").first().click();
  // 一覧に許可ルート外のパスが混ざっても、選んだ瞬間に弾いてダイアログは閉じない。
  await picker.getByRole("button", { name: "evil.gguf" }).click();
  await expect(page.getByText("選べるのは /data1tb と /data2tb の中だけです")).toBeVisible();
  await expect(picker).toBeVisible();

  await picker.getByRole("button", { name: "model.gguf" }).click();
  await expect(picker).toBeHidden();
  await expect(sheet.locator('[data-runtime-register-file="llama"] p[title="/data1tb/model.gguf"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    for (const expert of [false, true]) {
      test(`画面写真: ${width}px ${scheme} エキスパート${expert ? "入" : "切"}`, async ({ page, context }) => {
        test.setTimeout(180_000);
        await page.addInitScript((settings) => {
          try {
            localStorage.setItem("cd-theme", settings.scheme);
            localStorage.setItem("control-deck:commander-expert", settings.expert ? "1" : "0");
          } catch { /* 無視 */ }
        }, { scheme, expert });
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme: scheme });
        const { sheet } = await openRegisterSheet(page, context);
        const suffix = `${width}-${scheme}-${expert ? "expert" : "normal"}`;

        await expect(sheet.locator('[data-runtime-catalog="llama"]')).toBeVisible();
        await page.screenshot({ path: `test-results/models-runtime-common-llama-${suffix}.png` });

        await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
        await expect(sheet.locator('[data-runtime-settings="sly-radiance"]')).toBeVisible();
        await page.screenshot({ path: `test-results/models-runtime-common-radiance-${suffix}.png` });

        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      });
    }
  }
}
