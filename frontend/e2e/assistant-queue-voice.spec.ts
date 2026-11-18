// 目的: 生成中に送った要望が「送信待ち」に入り、編集・削除（元に戻す）・すべて取り消すができ、回答が終わって
//       サーバーが送ったら会話に出ること。音声入力は波形と時間が出て、黙っても止まらず、区切りごとに入力欄へ
//       文字が入り（送信はしない）、✓ で終わり、✕ でこの回の文字を戻すこと。320px と PC 幅ではみ出さないこと。
// 根拠: 2026-11-18 利用者「実行中に要望を送信したい。サーバー側で保存し、適切なタイミングで LLM に送る。複数の送信・
//       削除・編集・取り消し」「Claude のように波形が出て、確かめながら文字入力したい」「すぐ止まった。長時間を想定して」。
//       API・WebSocket・マイクは模擬する（LLM と音声認識を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const msg = (id: string, role: "user" | "assistant", content: string, status = "completed") => ({
  id, role, content, thinking: "", status, job_id: role === "assistant" ? `job-${id}` : null, model: "", error: "", meta: {},
  created_at: "2026-11-18T00:00:00Z",
});

type Item = { id: string; content: string; status: "queued" | "failed"; mode: string; error: string };

async function mockRunningConversation(page: Page) {
  const state = {
    messages: [msg("u1", "user", "最初の質問"), msg("a1", "assistant", "考えています", "generating")],
    items: [] as Item[], removed: [] as Item[], generating: "a1" as string | null, calls: [] as string[],
  };
  let seq = 0;
  await page.route(/\/api\/v1\/chat\/conversations\/q1\/messages$/, (route) =>
    route.fulfill({ json: { conversation: { id: "q1", title: "送信待ち" }, messages: state.messages } }));
  await page.route(/\/api\/v1\/chat\/conversations\/q1\/queue(\/.*)?$/, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/queue/, "");
    const method = req.method();
    state.calls.push(`${method} ${path}`);
    if (method === "GET") return route.fulfill({ json: { items: state.items, generating_message_id: state.generating, held: false } });
    if (method === "POST" && path === "") {
      const body = req.postDataJSON() as { content: string; mode: string };
      const item: Item = { id: `i${++seq}`, content: body.content, status: "queued", mode: body.mode, error: "" };
      state.items.push(item);
      return route.fulfill({ status: 201, json: { item, dispatched: null } });
    }
    if (method === "PATCH") {
      const id = path.slice(1);
      const item = state.items.find((i) => i.id === id)!;
      item.content = (req.postDataJSON() as { content: string }).content;
      return route.fulfill({ json: item });
    }
    if (method === "DELETE" && path === "") {
      const ids = state.items.map((i) => i.id);
      state.removed.push(...state.items);
      state.items = [];
      return route.fulfill({ json: { ids } });
    }
    if (method === "DELETE") {
      const id = path.slice(1);
      state.removed.push(...state.items.filter((i) => i.id === id));
      state.items = state.items.filter((i) => i.id !== id);
      return route.fulfill({ json: {} });
    }
    if (method === "POST" && path.endsWith("/restore")) {
      const id = path.split("/")[1];
      const item = state.removed.find((i) => i.id === id)!;
      state.items = [...state.items, item].sort((a, b) => a.id.localeCompare(b.id));
      return route.fulfill({ json: item });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  const sockets: Record<string, (data: object) => void> = {};
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    const id = ws.url().split("/").slice(-2)[0];
    sockets[id] = (data) => ws.send(JSON.stringify(data));
    ws.send(JSON.stringify({ type: "snapshot", content: state.messages.find((m) => m.id === id)?.content ?? "", thinking: "" }));
  });
  return { state, sockets };
}

for (const width of [320, 1280]) {
  test(`requests sent during a run wait, can be edited, removed and restored, then arrive (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const { state, sockets } = await mockRunningConversation(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "q1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByRole("button", { name: "止める" })).toBeVisible();          // 生成中
    const input = shell.locator("[data-assistant-input]");
    await input.fill("インダクタンスの最小値を1マイクロヘンリーに");
    await input.press("Enter");                                                     // Enter でも送信待ちへ
    await expect(input).toHaveValue("");
    await input.fill("電圧の最大値は");
    await shell.getByRole("button", { name: "送信待ちに入れる" }).click();
    const queue = shell.locator("[data-assistant-queue]");
    await expect(queue.getByText("送信待ち 2 件")).toBeVisible();
    await expect(queue.getByText("回答が終わったら送ります", { exact: false })).toBeVisible();

    await queue.getByRole("button", { name: "送信待ち 1 を編集" }).click();
    await queue.getByLabel("送信待ちの要望を書き直す").fill("インダクタンスの最小値は 1 µH");
    await queue.getByRole("button", { name: "保存", exact: true }).click();
    await expect(queue.getByText("インダクタンスの最小値は 1 µH")).toBeVisible();
    await queue.getByRole("button", { name: "送信待ち 2 を削除" }).click();
    await expect(queue.getByText("送信待ち 1 件")).toBeVisible();
    await queue.getByRole("button", { name: "元に戻す" }).click();
    await expect(queue.getByText("送信待ち 2 件")).toBeVisible();
    expect(state.calls).toEqual(expect.arrayContaining(["POST ", "PATCH /i1", "DELETE /i2", "POST /i2/restore"]));
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    // 入力欄の上に収まり、入力欄に隠れない
    const composer = (await shell.locator("[data-assistant-input-card]").boundingBox())!;
    const panel = (await queue.boundingBox())!;
    expect(panel.y + panel.height).toBeLessThanOrEqual(composer.y + 1);

    // 回答が終わり、サーバーが送信待ちを 1 つにまとめて送った
    state.messages = [msg("u1", "user", "最初の質問"), msg("a1", "assistant", "答え"),
      msg("u2", "user", "インダクタンスの最小値は 1 µH\n\n電圧の最大値は"), msg("a2", "assistant", "", "generating")];
    state.items = [];
    state.generating = "a2";
    sockets.a1({ type: "done", status: "completed" });
    await expect(shell.getByText("電圧の最大値は")).toBeVisible();
    await expect(queue).toHaveCount(0);
  });
}

/** マイクの代わり: 音（発振器）を出したり止めたりできる。window.__tone(true/false) */
async function fakeMicrophone(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __tone: (on: boolean) => void };
    let gain: GainNode | null = null;
    w.__tone = (on) => { if (gain) gain.gain.value = on ? 0.5 : 0; };
    navigator.mediaDevices.getUserMedia = async () => {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      gain = ctx.createGain();
      gain.gain.value = 0.5;
      const dest = ctx.createMediaStreamDestination();
      osc.connect(gain).connect(dest);
      osc.start();
      return dest.stream;
    };
  });
}

for (const width of [320, 1280]) {
  test(`voice input shows a waveform, keeps listening through silence and types each phrase (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await fakeMicrophone(page);
    await establishSession(page, context);
    let phrase = 0;
    await page.route("**/api/v1/chat/asr/status", (route) => route.fulfill({ json: { ready: true, installing: false, job_id: null } }));
    await page.route("**/api/v1/chat/asr/transcribe", (route) => route.fulfill({ json: { text: ["こんにちは。", "続きです。"][phrase++] ?? "" } }));
    let sent = 0;
    await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/send$/, (route) => { sent += 1; return route.abort(); });
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const input = shell.locator("[data-assistant-input]");
    await input.fill("前の文。");
    await shell.getByRole("button", { name: "音声で入力" }).click();
    const wave = shell.locator("[data-assistant-voice]");
    await expect(wave).toBeVisible();
    await expect(wave.getByText(/0:0[1-9]/)).toBeVisible();                        // 時間が進む
    const tall = await wave.locator("[role=img] span").evaluateAll((bars) => bars.filter((b) => (b as HTMLElement).offsetHeight > 6).length);
    expect(tall).toBeGreaterThan(3);                                              // 音に合わせて棒が伸びる
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.evaluate(() => (window as unknown as { __tone: (on: boolean) => void }).__tone(false));   // 黙る → 区切り
    await expect(input).toHaveValue("前の文。こんにちは。");
    await page.waitForTimeout(2500);                                              // 黙っても止まらない
    await expect(wave).toBeVisible();
    await page.evaluate(() => (window as unknown as { __tone: (on: boolean) => void }).__tone(true));
    await page.waitForTimeout(1600);
    await shell.getByRole("button", { name: "音声入力を終える" }).click();          // ✓: 残りを文字にして終わる
    await expect(input).toHaveValue("前の文。こんにちは。続きです。");
    await expect(wave).toHaveCount(0);
    expect(sent).toBe(0);                                                         // 自動では送らない

    // ✕ で取り消すと、この回に入れた文字を戻す
    phrase = 0;
    await shell.getByRole("button", { name: "音声で入力" }).click();
    await page.evaluate(() => (window as unknown as { __tone: (on: boolean) => void }).__tone(false));
    await page.waitForTimeout(300);
    await page.evaluate(() => (window as unknown as { __tone: (on: boolean) => void }).__tone(true));
    await page.waitForTimeout(1700);
    await page.evaluate(() => (window as unknown as { __tone: (on: boolean) => void }).__tone(false));
    await expect(input).toHaveValue("前の文。こんにちは。続きです。こんにちは。");
    await shell.getByRole("button", { name: "音声入力を取り消す" }).click();
    await expect(input).toHaveValue("前の文。こんにちは。続きです。");
  });
}
