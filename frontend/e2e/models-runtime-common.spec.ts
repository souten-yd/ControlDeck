import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";
import { openCardAndAct, withModels } from "./support/catalog";

/** Models の共通ランタイム部品（目録・ファイル登録・設定）の受け入れ E2E。
 *
 *  目的（Nexarch #14 / 2026-11-28、同日補強）:
 *   - llama.cpp / Strata / Radiance（Sly 派生を含む）/ Maya が、登録シートで
 *     同じ順番・同じ見出しの共通 3 部品（目録 → ファイル登録 → 設定）を出すこと。
 *     目録の見出しは「目録から取得」へ統一する要求なのでその文言に期待を固定する
 *     （docs/PLAN-model-catalog.md「共通の並び」）。レビュー #25 / 2026-11-28 の UI 修正で
 *     旧「目録から取る／探す」と Maya の二重目録は解消済み（緩めずに同じ文言へ固定する）。
 *   - 設定の節が ①モデル ②コンテキストの長さ ③同時に処理する数 ④速さの調整 ⑤起動 の順で、
 *     ⑥エキスパートはエキスパート入のときだけ出ること。5 ランタイムすべてで検査する。
 *   - 取得ジョブの「停止」→「再開」と「取り消し」が API のモックで動くこと
 *     （.part を残す/再開・取り消しで消す意味は docs/PLAN-model-catalog.md「決めた意味」を照合）。
 *     確認パネルの「取得する」は目録の行と同名なので、確認パネル固有の「やめる」を待ってから
 *     押す（既知の競合: 行のボタンを誤クリックしてジョブが始まらない）。
 *   - ファイル登録の FilePicker のルートが /data1tb と /data2tb だけであること
 *     （RuntimeRegisterFromFile.MODEL_FILE_PICKER_SOURCE と defense in depth）。
 *   - 320/1280px × ライト/ダーク × エキスパート切/入の画面写真を 5 ランタイム分残すこと。
 *
 *  根拠: #11（Radiance・Maya）/#12（Strata・llama.cpp）で共通部品へ寄せた後の確認。
 *  API は page.route で差し替え、本物の設定・外部への取得は一切行わない。目録モックの
 *  Radiance も acquire="hf"（supported かつ repo ありという実 API 契約どおり。#19 で実装も hf 化）。
 *
 *  更新: レビュー #25 / 2026-11-28 の要修正。目録の見出し「目録から取得」を維持し、
 *        停止済み（interrupted + can_resume）のジョブにも「取り消し」を出し、破壊操作なので
 *        確認ダイアログ（「取得を取り消す」/「取り消す」）を挟む導線へ追随した。
 *  実行コマンド（この箱では /dev/shm が読み取り専用なので一時 shim を PYTHONPATH に付ける）:
 *      env -u CONTROL_DECK_CONFIG PYTHONPATH=<一時 shim> \
 *        PLAYWRIGHT_BROWSERS_PATH=/data1tb/ControlDeck/data/cache/ms-playwright \
 *        ./deck.sh e2e models-runtime-common.spec.ts
 */
const RUNTIME_BUTTONS = ["llama.cpp", "Strata", "Radiance", "Maya"] as const;
const CATALOG_HEADING = "目録から取得";
const FILE_HEADING = "ファイルから登録";
const SECTION_LABELS = ["モデル", "コンテキストの長さ", "同時に処理する数", "速さの調整", "起動"] as const;
/** 全ランタイムで揃えるべき見出し。summary には説明文が続くので先頭だけを厳しく照合する。 */
const headingStart = (label: string) => new RegExp(`^${label}`);
const COMMON_HEADINGS = [CATALOG_HEADING, FILE_HEADING, ...SECTION_LABELS] as const;
const HEADING_PATTERNS = COMMON_HEADINGS.map(headingStart);
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

const catalog = withModels({
  runtimes: RUNTIME_IDS.map((id) => ({
    id,
    name: id,
    installed: true,
    formats: [],
    // supported かつ repo ありの行は acquire="hf"（実 API 契約どおり。#19 で Radiance も hf 化）。
    acquire: "hf",
    entries: [catalogEntry(`e2e-${id}`, "hf")],
    catalog_ref: `/api/v1/models/catalog/${id}`,
  })),
});

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
  // FilePicker は /files/list?path=... を叩く。glob は末尾まで一致するので * を付ける。
  await page.route("**/api/v1/files/list*", (route) => {
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
  // 取り消しは canceled（終端・再開不可）になり、途中のファイルを消してカードも片付けられる。
  const calls: string[] = [];
  let running = true;
  let canceled = false;
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
    status: canceled ? "canceled" : running ? "running" : "interrupted",
    progress: { status: canceled ? "取り消しました" : running ? "取得中" : "停止中（続きから再開できます）", completed: 4, total: 10 },
    error: "", can_resume: !running && !canceled,
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
  // 取り消しは既存の POST /jobs/{id}/cancel のまま（再開の記録と .part を消す）。
  await page.route("**/api/v1/jobs/job-1/cancel", (route) => {
    calls.push("cancel"); canceled = true;
    return route.fulfill({ json: { ok: true } });
  });
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

test("モデル登録: 4 ランタイムが同じ順番で目録 → ファイル登録 → 設定を出す", async ({ page, context }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const { sheet } = await openRegisterSheet(page, context);

  const buttons = sheet.getByRole("button", { name: /^(llama\.cpp|Strata|Radiance|Maya)$/ });
  await expect(buttons).toHaveText([...RUNTIME_BUTTONS]);

  // llama.cpp / Strata は共通部品を SegmentedTabs で切り替える。目録 → ファイルの順。
  await expect(sheet.getByRole("button", { name: CATALOG_HEADING })).toHaveAttribute("aria-pressed", "true");
  await expect(sheet.locator('[data-runtime-catalog="llama"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-model="m-e2e-llama"]')).toContainText("E2E e2e-llama");
  await expect(sheet.locator('[data-runtime-catalog="llama"]')).toHaveAttribute("data-expert", "off");
  await sheet.getByRole("button", { name: FILE_HEADING, exact: true }).click();
  await expect(sheet.locator('[data-runtime-register-file="llama"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-register-file="llama"]')).toHaveAttribute("data-expert", "off");
  await expect(sheet.locator('[data-runtime-register-file="llama"]').getByRole("button", { name: "モデルのファイルを選ぶ" })).toBeVisible();

  // ランタイムを切り替えてもタブの状態が残ることがあるので、毎回 目録 を選び直す。
  await sheet.getByRole("button", { name: "Strata", exact: true }).click();
  await sheet.getByRole("button", { name: CATALOG_HEADING }).click();
  await expect(sheet.locator('[data-runtime-catalog="strata"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-model="m-e2e-strata"]')).toContainText("E2E e2e-strata");
  await sheet.getByRole("button", { name: FILE_HEADING, exact: true }).click();
  await expect(sheet.locator('[data-runtime-register-file="strata"]')).toBeVisible();

  // Radiance は Sly 派生が既定。目録・ファイルの見出しは閉じた <details> の <summary> なので、
  // 厳しい見出し列を確認してから開いて中身を見る。
  await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([...HEADING_PATTERNS]);
  await expect(sheet.locator('[data-runtime-catalog="sly-radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-catalog-model="m-e2e-sly-radiance"]')).toContainText("E2E e2e-sly-radiance");
  await sheet.locator("summary").filter({ hasText: headingStart(FILE_HEADING) }).click();
  await expect(sheet.locator('[data-runtime-register-file="sly-radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="sly-radiance"]')).toBeVisible();
  // スイッチを切ると元の Radiance になるが、共通部品の見出し・並びは変わらない。
  await sheet.getByRole("switch", { name: "Sly-Radiance で登録" }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([...HEADING_PATTERNS]);
  await expect(sheet.locator('[data-runtime-catalog="radiance"]')).toBeVisible();
  await sheet.locator("summary").filter({ hasText: headingStart(FILE_HEADING) }).click();
  await expect(sheet.locator('[data-runtime-register-file="radiance"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="radiance"]')).toBeVisible();

  // Maya も同じ見出し・並び（「Maya の目録」のような二重目録は要求違反として許容しない）。
  await sheet.getByRole("button", { name: "Maya", exact: true }).click();
  await expect(sheet.locator("h3, summary")).toHaveText([...HEADING_PATTERNS]);
  // Maya の目録だけは畳んである（Maya 自身の量子化の一覧が主・2026-11-28）
  await sheet.locator("summary").filter({ hasText: headingStart(CATALOG_HEADING) }).click();
  await expect(sheet.locator('[data-runtime-catalog="maya"]')).toBeVisible();
  await sheet.locator("summary").filter({ hasText: headingStart(FILE_HEADING) }).click();
  await expect(sheet.locator('[data-runtime-register-file="maya"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-settings="maya"]')).toBeVisible();
});

type Sheet = ReturnType<Page["getByRole"]>;

/** 登録シートを目的のランタイムの共通設定まで進める。
 *  llama.cpp / Strata は共通の登録パネルなのでファイル登録 → 設定の順に進み、
 *  Radiance 系・Maya はランタイムボタン（Sly はスイッチ）で直接設定が出る。 */
async function reachCommonSettings(page: Page, sheet: Sheet, runtime: (typeof RUNTIME_IDS)[number]) {
  if (runtime === "llama" || runtime === "strata") {
    if (runtime === "strata") await sheet.getByRole("button", { name: "Strata", exact: true }).click();
    await sheet.getByRole("button", { name: FILE_HEADING, exact: true }).click();
    await sheet.getByRole("button", { name: "モデルのファイルを選ぶ" }).click();
    const picker = page.getByRole("dialog", { name: FILE_PICKER_TITLE });
    // initialPath が残るとルート一覧ではなくフォルダ内が開く。どちらでもモデルを選べるようにする。
    const rootButton = picker.getByRole("button", { name: "/data1tb", exact: true });
    await expect(rootButton).toBeVisible();
    await rootButton.click();
    await expect(picker.getByRole("button", { name: "model.gguf" })).toBeVisible();
    await picker.getByRole("button", { name: "model.gguf" }).click();
    await expect(picker).toBeHidden();
    await sheet.locator(`[data-runtime-register-file="${runtime}"]`).getByRole("button", { name: "登録する" }).click();
  } else if (runtime === "sly-radiance" || runtime === "radiance") {
    await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
    if (runtime === "radiance") await sheet.getByRole("switch", { name: "Sly-Radiance で登録" }).click();
  } else {
    await sheet.getByRole("button", { name: "Maya", exact: true }).click();
  }
  const settings = sheet.locator(`[data-runtime-settings="${runtime}"]`);
  await expect(settings).toBeVisible();
  return settings;
}

for (const expert of [false, true]) {
  test(`共通設定: 5 ランタイムで見出しの並びが同じで、エキスパートは${expert ? "入" : "切"}のときだけ出る`, async ({ page, context }) => {
    await page.addInitScript((on) => {
      try { localStorage.setItem("control-deck:commander-expert", on ? "1" : "0"); } catch { /* 無視 */ }
    }, expert);
    await page.setViewportSize({ width: 1280, height: 900 });
    const { sheet } = await openRegisterSheet(page, context);

    // llama → Strata → Sly → Radiance → Maya の順に切り替え、毎回同じ ①〜⑥ を確かめる。
    // ランタイムボタンはシート上部に常にあるので、設定から戻らずに次へ移れる。
    for (const runtime of RUNTIME_IDS) {
      const settings = await reachCommonSettings(page, sheet, runtime);
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

  const entry = sheet.locator('[data-catalog-model="m-e2e-llama"]');
  await openCardAndAct(entry);   // 2026-11-28: 確認の枠は無くし、カードの中の主のボタン 1 つで始める

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

test("取得ジョブ: 取り消しの API モックと实际操作（320px）", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const { sheet, jobs } = await openRegisterSheet(page, context);

  const entry = sheet.locator('[data-catalog-model="m-e2e-llama"]');
  await openCardAndAct(entry);   // 2026-11-28: 確認の枠は無くし、カードの中の主のボタン 1 つで始める

  const running = sheet.locator('[data-acquire-job="job-1"]');
  await expect(running).toContainText("取得中");
  // 実行中は「停止」（記録と .part を残す）と「取り消し」（消して終端）の両方が出る。
  await expect(running.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
  await expect(running.getByRole("button", { name: "取り消し", exact: true })).toBeEnabled();

  await running.getByRole("button", { name: "取り消し", exact: true }).click();
  // 取り消しは破壊操作（途中の .part を消す）なので確認ダイアログを挟む。
  const confirm = page.getByRole("alertdialog", { name: "取得を取り消す" });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByText(/\.part/)).toBeVisible();
  await confirm.getByRole("button", { name: "取り消す", exact: true }).click();
  await expect(page.getByText("取り消しました（途中のファイルは削除されます）")).toBeVisible();
  expect(jobs.calls).toContain("cancel");
  // 取り消しは終端: 再開の記録が残らず、ジョブカードも片付けられる（停止とは別の道）。
  await expect(sheet.locator('[data-acquire-job="job-1"]')).toHaveCount(0);
  expect(jobs.calls).not.toContain("stop");
  expect(jobs.calls).not.toContain("resume");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("取得ジョブ: 停止済みのジョブも取り消せる（確認ダイアログ・320px）", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const { sheet, jobs } = await openRegisterSheet(page, context);

  const entry = sheet.locator('[data-catalog-model="m-e2e-llama"]');
  await openCardAndAct(entry);   // 2026-11-28: 確認の枠は無くし、カードの中の主のボタン 1 つで始める
  const running = sheet.locator('[data-acquire-job="job-1"]');
  await running.getByRole("button", { name: "停止", exact: true }).click();
  await expect(running).toContainText("停止中（続きから再開できます）");
  // 停止しても再開の記録（.part）は残る。取り消しはその記録を消す破壊操作なので確認を挟む。
  await expect(running.getByRole("button", { name: "再開", exact: true })).toBeEnabled();
  await expect(running.getByRole("button", { name: "取り消し", exact: true })).toBeEnabled();

  const confirm = page.getByRole("alertdialog", { name: "取得を取り消す" });
  await running.getByRole("button", { name: "取り消し", exact: true }).click();
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "キャンセル" }).click();
  await expect(sheet.locator('[data-acquire-job="job-1"]')).toBeVisible();
  expect(jobs.calls).not.toContain("cancel");

  await running.getByRole("button", { name: "取り消し", exact: true }).click();
  await confirm.getByRole("button", { name: "取り消す", exact: true }).click();
  await expect(page.getByText("取り消しました（途中のファイルは削除されます）")).toBeVisible();
  expect(jobs.calls).toContain("cancel");
  // 停止している間にカードが terminal 扱いになった後なので、カードは残るが再開はできない。
  await expect(sheet.locator('[data-acquire-job="job-1"]')).toContainText("取り消しました");
  await expect(running.getByRole("button", { name: "再開", exact: true })).toHaveCount(0);
});

test("ファイル登録の FilePicker: ルートは /data1tb と /data2tb だけ", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const { sheet } = await openRegisterSheet(page, context);
  await sheet.getByRole("button", { name: FILE_HEADING, exact: true }).click();
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

// 5 ランタイム × 320/1280px × 明暗 × エキスパート切/入。
// 共通 2 ランタイムは目録の初期表示、Radiance 系・Maya は共通設定の表示を撮る。
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
        await expect(sheet.locator(".animate-pulse")).toHaveCount(0);   // 読み込みの印が消えてから撮る
        await page.screenshot({ path: `test-results/models-runtime-common-llama-${suffix}.png` });

        await sheet.getByRole("button", { name: "Strata", exact: true }).click();
        await expect(sheet.locator('[data-runtime-catalog="strata"]')).toBeVisible();
        await expect(sheet.locator(".animate-pulse")).toHaveCount(0);   // 読み込みの印が消えてから撮る
        await page.screenshot({ path: `test-results/models-runtime-common-strata-${suffix}.png` });

        await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
        await expect(sheet.locator('[data-runtime-settings="sly-radiance"]')).toBeVisible();
        await expect(sheet.locator(".animate-pulse")).toHaveCount(0);   // 読み込みの印が消えてから撮る
        await page.screenshot({ path: `test-results/models-runtime-common-radiance-${suffix}.png` });

        await sheet.getByRole("switch", { name: "Sly-Radiance で登録" }).click();
        await expect(sheet.locator('[data-runtime-settings="radiance"]')).toBeVisible();
        await expect(sheet.locator(".animate-pulse")).toHaveCount(0);   // 読み込みの印が消えてから撮る
        await page.screenshot({ path: `test-results/models-runtime-common-radiance-plain-${suffix}.png` });

        await sheet.getByRole("button", { name: "Maya", exact: true }).click();
        await expect(sheet.locator('[data-runtime-settings="maya"]')).toBeVisible();
        await expect(sheet.locator(".animate-pulse")).toHaveCount(0);   // 読み込みの印が消えてから撮る
        await page.screenshot({ path: `test-results/models-runtime-common-maya-${suffix}.png` });

        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      });
    }
  }
}
