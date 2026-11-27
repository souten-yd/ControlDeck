// 目的: OpenCode の会話から USB 機器（M5Stack など）をプロジェクトの箱へ渡せること。機器が 1 台以上つながっているときだけ
//       作業フォルダの横に「USB」の札が出て、押すとシート（機器の一覧・切／自動／選ぶ・道具の有無）が開く。
//       既定は切で、「自動」を押すと PUT が飛ぶ。返事に Permission denied が出たら、その下に案内が出て同じシートが開く。
//       つながっている機器が無ければ札は出ない。390px・1280px とも横にはみ出さない。
// 根拠: 2026-11-27 利用者「OpenCode で開発したソフトが USB 経由で書き込めない（M5Paper）」「M5 や iOS、Android の開発」。
//       API と WebSocket は模擬する（実機・箱を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const M5 = { kind: "serial", id: "usb:1a86:55d4:52D4000426", label: "USB Single Serial", path: "/dev/ttyCH343USB0", vendor: "", serial_tail: "0426",
  guess: "M5Stack など（CH9102）", available: true, note: "" };
const IPHONE = { kind: "ios", id: "05ac:12a8:00008030AAAA", label: "iPhone", path: "/var/run/usbmuxd", vendor: "Apple Inc.", serial_tail: "AAAA",
  guess: "", available: false, note: "ホストで usbmuxd を動かす必要があります" };

async function mock(page: Page, devices: object[]) {
  const state = { puts: [] as Record<string, unknown>[], mode: "off" };
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "paper", path: "/CodeDEV/paper", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/opencode\/workspaces(\/[^?]+)?(\?.*)?$/, (route) => route.fulfill({ json: { workspaces: [] } }));
  await page.route(/\/api\/v1\/opencode\/devices$/, (route) => route.fulfill({ json: { devices, usbmuxd: false, tools: [
    { name: "esptool", ok: false, hint: "pip install esptool（ESP32・M5Stack の書き込み）" }, { name: "adb", ok: true, hint: "" }] } }));
  await page.route(/\/api\/v1\/opencode\/projects\/paper\/usb$/, (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { mode: string };
      state.puts.push(body);
      state.mode = body.mode;
      return route.fulfill({ json: { mode: body.mode, selected: [], restart_needed: false } });
    }
    return route.fulfill({ json: { mode: state.mode, selected: [], restart_needed: false } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/u1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "u1", title: "M5Paper" },
    messages: [{ id: "u0", role: "user", content: "書き込んで", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-27T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
      { id: "a0", role: "assistant", content: "書き込めませんでした: could not open port /dev/ttyCH343USB0: [Errno 13] Permission denied", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-27T00:00:01Z",
        meta: { mode: "code", project_path: "/CodeDEV/paper", agent: "build" } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/u1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  return state;
}

for (const width of [390, 1280]) {
  test(`USB chip, sheet and error hint (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page, [M5, IPHONE]);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "u1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("Permission denied")).toBeVisible();
    await expect(shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）")).toHaveValue("paper");
    const chip = shell.getByTestId("usb-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toHaveText("USB");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await chip.click();
    const sheet = page.getByTestId("usb-sheet");
    await expect(sheet).toContainText("M5Stack など（CH9102）");
    await expect(sheet).toContainText("/dev/ttyCH343USB0");
    await expect(sheet).toContainText("ホストで usbmuxd を動かす必要があります");
    await expect(sheet.getByRole("radio", { name: "切" })).toHaveAttribute("aria-checked", "true");
    await expect(sheet).toContainText("esptool");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/assistant-usb-${width}-sheet.png` });
    await sheet.getByRole("radio", { name: "自動（全部）" }).click();
    await expect.poll(() => state.puts.length).toBe(1);
    expect(state.puts[0]).toMatchObject({ mode: "auto", selected: [] });
    await expect(chip).toHaveText("USB・渡す");
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    // 返事のエラーの下の案内から、同じシートが開く
    const hint = shell.getByTestId("usb-hint");
    await expect(hint).toBeVisible();
    await page.screenshot({ path: `test-results/assistant-usb-${width}-hint.png` });
    await hint.click();
    await expect(page.getByTestId("usb-sheet")).toBeVisible();
  });
}

test("no USB chip when no device is connected", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  await mock(page, []);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "u1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）")).toHaveValue("paper");
  await expect(shell.getByTestId("usb-chip")).toHaveCount(0);
});
