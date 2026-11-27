// 目的: 設定の機能と拡張に「Android SDK（コマンドライン）」の行があり、ライセンスへの同意にチェックを入れるまで「導入」を押せず、
//       押すと同意つきで install-jobs へ送られること（取得は利用者の「導入」のときだけ）。入っていれば入っている部品が出る。
//       機器のシートでは、Android の機器を渡す設定でホストの adb が動いていれば「ホストの adb が機器を使っています」と
//       「ホストの adb を止める」が出て、押すと POST が飛ぶ。Android の道具が無ければ「設定で入れる」リンクが出る。
//       390px・1280px とも横にはみ出さない。
// 根拠: 2026-11-27 利用者「Android とかにも対応してね」（M5・iOS・Android の開発を OpenCode で）。API は page.route で模擬する
//       （Google から何も取得しない・adb を止めない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const catalog = { platforms: ["34", "35"], build_tools: ["34.0.0", "35.0.0"], defaults: { platform: "34", build_tools: "34.0.0" },
  license_url: "https://developer.android.com/studio/terms" };
const base = { route_gated: false, requires: "", requires_installed: true, available: true, enabled: false, requested_enabled: true,
  preview: false, id: "android-sdk", name: "Android SDK（コマンドライン）", kind: "android-sdk", category: "cli",
  summary: "Android の開発用（adb・fastboot・sdkmanager・build-tools）。ライセンスへの同意が要る" };
const missing = { ...base, installed: false, managed: false, health: "not-installed", version: "", error: "", executable: "",
  android_sdk: { installed: false, root: "/data/tools/android-sdk", cmdline_tools: "", platforms: [], build_tools: [], licenses_accepted: false, java: true, catalog } };
const present = { ...base, installed: true, managed: true, enabled: true, health: "healthy", version: "15859902", error: "", executable: "/data/tools/android-sdk/platform-tools/adb",
  android_sdk: { ...missing.android_sdk, installed: true, cmdline_tools: "15859902", platforms: ["34"], build_tools: ["34.0.0"], licenses_accepted: true } };

async function mockFeatures(page: Page, feature: object) {
  await page.route(/\/api\/v1\/features$/, (route) => route.fulfill({ json: [feature] }));
  await page.route(/\/api\/v1\/features\/auto-update$/, (route) => route.fulfill({ json: { enabled: false, time: "04:00", last: {}, running: false } }));
}

for (const width of [390, 1280]) {
  test(`機能と拡張: Android SDK（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await mockFeatures(page, missing);
    const posts: Record<string, unknown>[] = [];
    await page.route(/\/api\/v1\/features\/android-sdk\/install-jobs$/, (route) => {
      posts.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ status: 201, json: { job_id: "job-android" } });
    });
    await page.route(/\/api\/v1\/jobs\/job-android$/, (route) => route.fulfill({ json: { id: "job-android", status: "running", progress: { status: "導入中" } } }));
    await page.goto("/settings/features");
    const card = page.locator("#feature-android-sdk");
    await expect(card.getByText("Android SDK（コマンドライン）").first()).toBeVisible();
    const panel = card.getByTestId("android-sdk-panel");
    const install = panel.getByRole("button", { name: "導入", exact: true });
    await expect(install).toBeDisabled();                       // 同意するまで押せない
    await expect(panel).toContainText("developer.android.com/studio/terms");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-android-sdk-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await panel.getByRole("checkbox").check();
    await expect(install).toBeEnabled();
    await install.click();
    await expect.poll(() => posts.length).toBe(1);
    expect(posts[0]).toMatchObject({ accept_licenses: true, android_platform: "34", android_build_tools: "34.0.0" });
  });
}

test("機能と拡張: Android SDK が入っているとき", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 900 });
  await establishSession(page, context);
  await mockFeatures(page, present);
  await page.goto("/settings/features");
  const card = page.locator("#feature-android-sdk");
  await expect(card.getByTestId("android-sdk-installed")).toContainText("build-tools 34.0.0");
  await expect(card.getByTestId("android-sdk-panel").getByRole("button", { name: "入れ直す（修復）" })).toBeDisabled();
});

const PIXEL = { kind: "adb", id: "18d1:4ee7:PIXEL0001", label: "Pixel 8", path: "/dev/bus/usb/001/007", vendor: "Google", serial_tail: "0001", guess: "", available: true, note: "" };

for (const width of [390, 1280]) {
  test(`機器のシート: ホストの adb の案内と設定で入れるリンク（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = { stopped: 0, running: true, mode: "off" };
    await page.route(/\/api\/v1\/meta$/, async (route) => {
      const response = await route.fetch();
      const meta = await response.json();
      meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
      return route.fulfill({ response, json: meta });
    });
    await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
    await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
    await page.route(/\/api\/v1\/opencode\/workspaces(\/[^?]+)?(\?.*)?$/, (route) => route.fulfill({ json: { workspaces: [] } }));
    await page.route(/\/api\/v1\/opencode\/devices$/, (route) => route.fulfill({ json: {
      devices: [PIXEL], usbmuxd: false, tools: [{ name: "fastboot", ok: false, hint: "設定の機能で Android SDK を入れる" }],
      android_sdk: { installed: false }, host_adb: { running: state.running, pids: [4242], listening: true } } }));
    await page.route(/\/api\/v1\/opencode\/devices\/host-adb\/stop$/, (route) => {
      state.stopped += 1;
      state.running = false;
      return route.fulfill({ json: { ok: true, status: { running: false, pids: [], listening: false } } });
    });
    await page.route(/\/api\/v1\/opencode\/projects\/app\/usb$/, (route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as { mode: string };
        state.mode = body.mode;
      }
      return route.fulfill({ json: { mode: state.mode, selected: [], restart_needed: false } });
    });
    await page.route(/\/api\/v1\/chat\/conversations\/a1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "a1", title: "Android" },
      messages: [{ id: "u0", role: "user", content: "ビルドして", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-27T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
        { id: "a0", role: "assistant", content: "了解", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-27T00:00:01Z", meta: { mode: "code", project_path: "/CodeDEV/app", agent: "build" } }],
    } }));
    await page.route(/\/api\/v1\/chat\/conversations\/a1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "a1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await shell.getByTestId("usb-chip").click();
    const sheet = page.getByTestId("usb-sheet");
    await expect(sheet.getByTestId("usb-host-adb")).toHaveCount(0);          // 切のときは出さない
    await expect(sheet.getByTestId("usb-sdk-link")).toContainText("設定で入れる");
    await expect(sheet.getByRole("link", { name: "設定で入れる" })).toHaveAttribute("href", /\/settings\/features#feature-android-sdk/);
    await sheet.getByRole("radio", { name: "自動（全部）" }).click();
    const banner = sheet.getByTestId("usb-host-adb");
    await expect(banner).toContainText("ホストの adb が機器を使っています");
    await page.screenshot({ path: `test-results/assistant-usb-android-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await sheet.getByTestId("usb-host-adb-stop").click();
    await expect.poll(() => state.stopped).toBe(1);
  });
}
