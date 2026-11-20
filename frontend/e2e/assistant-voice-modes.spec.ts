// 目的: AI アシスタントの TTS 返答（回答の主要な文言を声で読む）と会話モード（話して黙ると送り、回答を読み、
//       また聞く。停止まで交互に続く）。読む文言からコード・表・URL・出典を除くこと。読み上げ中はマイクの音を
//       送らないこと。320px と PC 幅ではみ出さないこと。
// 根拠: 2026-11-19 利用者「TTS 返答モードではチャットの主要な文言を音声で回答」「会話モードは一定の入力停止を
//       検知して LLM が回答し、停止ボタンを押すまで交互に会話が続く」。
//       API・WebSocket・マイク・音声合成は模擬する（LLM・音声認識・TTS を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";
import { nextSpoken, speakableText, speechChunks, stablePrefix, voicedRange } from "../src/features/workflows/speakable";

test("speakable text keeps the main words and drops code, tables, links and sources", () => {
  const spoken = speakableText([
    "## 結論", "明日は**晴れ**です [1]。", "- 最高気温は 20 度", "",
    "```python", "print('x')", "```", "| 日 | 天気 |", "|---|---|", "| 月 | 晴 |",
    "詳しくは [気象庁](https://www.jma.go.jp/) を見てください https://example.com", "",
    "## 出典", "[1] https://example.com/weather",
  ].join("\n"));
  expect(spoken).toBe("結論。明日は晴れです。最高気温は 20 度。コードは画面に表示しています。表は画面に表示しています。詳しくは 気象庁 を見てください。");
  const long = speakableText("あ".repeat(50) + "。".repeat(1) + "い".repeat(2000), 100);
  expect(long.endsWith("続きは画面をご覧ください。")).toBe(true);
  expect(speechChunks("一。二。三。")).toEqual(["一。二。三。"]);
  // 絵文字とその後ろの見えない文字、英字の出典の印は読まない（2026-11-19 本番: 「☀️」で合成が失敗した）
  expect(speakableText("いいお天気ですね☀️ 家族👨‍👩‍👧で。きているそうです [RK]。言ってます [RB][RC]。資料（[R1]〜[RA]）を検索"))
    .toBe("いいお天気ですね 家族で。きているそうです。言ってます。資料を検索。");
  expect(speechChunks(`${"長".repeat(70)}。${"次".repeat(70)}。`)[0]).toBe(`${"長".repeat(70)}。`);
});

/** マイクの代わり: window.__tone(true/false) で音を出したり止めたりする */
async function fakeMicrophone(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __tone: (on: boolean) => void };
    let gain: GainNode | null = null;
    w.__tone = (on) => { if (gain) gain.gain.value = on ? 0.5 : 0; };
    navigator.mediaDevices.getUserMedia = async () => {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      gain = ctx.createGain();
      gain.gain.value = 0;
      const dest = ctx.createMediaStreamDestination();
      osc.connect(gain).connect(dest);
      osc.start();
      return dest.stream;
    };
  });
}

/** 模擬の TTS が返す WAV: 0.4 秒の無音・0.3 秒の音・0.4 秒の無音（Supertonic 3 と同じく前後に無音が付く） */
function silentWav(): Buffer {
  const lead = 17640, tone = 13230, samples = lead * 2 + tone;
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + samples * 2, 4); buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(44100, 24); buffer.writeUInt32LE(88200, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36); buffer.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < tone; i++) buffer.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * 440 * i) / 44100)), 44 + (lead + i) * 2);
  return buffer;
}

async function mockChat(page: Page, transcripts: string[]) {
  const state = { sent: [] as string[], styles: [] as string[], spoken: [] as string[], transcribed: 0, messages: [] as object[] };
  let turn = 0;
  await page.route("**/api/v1/chat/asr/status", (route) => route.fulfill({ json: { ready: true, installing: false, job_id: null } }));
  await page.route("**/api/v1/chat/asr/transcribe", (route) => {
    state.transcribed += 1;
    const text = transcripts.shift();
    return text ? route.fulfill({ json: { text } }) : route.fulfill({ status: 422, json: { detail: "音声を認識できませんでした" } });
  });
  await page.route("**/api/v1/chat/route", (route) => route.fulfill({ json: { mode: "chat", reason: "", steps: [], max_iterations: 1, decided_by: "rule" } }));
  await page.route(/\/api\/v1\/chat\/conversations$/, (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 201, json: { id: "v1", title: "" } }) : route.fallback());
  await page.route(/\/api\/v1\/chat\/conversations\/v1\/messages$/, (route) =>
    route.fulfill({ json: { conversation: { id: "v1", title: "音声" }, messages: state.messages } }));
  await page.route(/\/api\/v1\/chat\/conversations\/v1\/queue$/, (route) =>
    route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/chat\/conversations\/v1\/send$/, (route) => {
    const body = route.request().postDataJSON() as { content: string; voice_style?: string };
    state.sent.push(body.content);
    state.styles.push(body.voice_style ?? "");
    turn += 1;
    return route.fulfill({ json: { assistant_message_id: `a${turn}`, user_message_id: `u${turn}` } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    const n = Number(ws.url().split("/").slice(-2)[0].slice(1));
    ws.send(JSON.stringify({ type: "snapshot", content: "", thinking: "" }));
    setTimeout(() => {
      ws.send(JSON.stringify({ type: "delta", content: `回答${n}です。\n\`\`\`\ncode()\n\`\`\`\n## 出典\n[1] https://example.com` }));
      ws.send(JSON.stringify({ type: "done", status: "completed" }));
      void ws.close();   // サーバーも done の後に閉じる（chat_persist の stream）
    }, 200);
  });
  await page.route("**/api/v1/speech/tts", (route) => {
    state.spoken.push((route.request().postDataJSON() as { text: string }).text);
    return route.fulfill({ body: silentWav(), contentType: "audio/wav" });
  });
  return state;
}

const tone = (page: Page, on: boolean) => page.evaluate((value) => (window as unknown as { __tone: (on: boolean) => void }).__tone(value), on);

for (const width of [320, 1280]) {
  test(`conversation mode sends after a pause, speaks the reply and listens again until stopped (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    test.setTimeout(60_000);
    await page.setViewportSize({ width, height: 800 });
    await fakeMicrophone(page);
    await establishSession(page, context);
    await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.removeItem("cd-assistant-tts-reply"); localStorage.removeItem("cd-assistant-voice-style"); });
    const transcriptsLeft = ["明日の天気は？", "ありがとう。"];
    const state = await mockChat(page, transcriptsLeft);
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await shell.getByRole("button", { name: "添付・機能・権限" }).click();
    await page.locator('[data-plus-action="talk"]').click();
    const phase = shell.locator("[data-talk-phase]");
    await expect(phase).toHaveAttribute("data-talk-phase", "listening");
    await expect(shell.locator("[data-speaker-toggle]")).toHaveAttribute("data-speaker-toggle", "on");   // 会話は声で返す
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `${process.env.CONTROL_DECK_E2E_SHOTS ?? "/tmp"}/talk-listening-${width}.png` });

    // 1 回目: 話して黙る → 送る → 回答を読む（コードと出典は読まない）→ また聞く
    await tone(page, true);
    await page.waitForTimeout(1700);
    await tone(page, false);
    await expect.poll(() => state.sent).toEqual(["明日の天気は？"]);
    // 会話モードの発言には回答傾向を付けて送る（既定はフレンドリー。2026-11-19 利用者の依頼）
    expect(state.styles).toEqual(["friendly"]);
    await expect.poll(() => state.spoken.join("")).toContain("回答1です。");
    expect(state.spoken.join("")).not.toMatch(/出典|https|code\(\)/);
    await expect(phase).toHaveAttribute("data-talk-phase", "listening", { timeout: 10_000 });
    await expect(shell.getByText("回答1です。").first()).toBeVisible();

    // 2 回目: AI 設定で回答傾向を変えると、次の発言から使う
    await shell.getByRole("button", { name: "AI設定を開く" }).click();
    await shell.getByLabel("会話モードの回答傾向").selectOption("teacher");
    await shell.getByRole("button", { name: "AI設定を閉じる" }).click();
    await tone(page, true);
    await page.waitForTimeout(1700);
    await tone(page, false);
    await expect.poll(() => state.sent).toEqual(["明日の天気は？", "ありがとう。"]);
    expect(state.styles).toEqual(["friendly", "teacher"]);
    await expect(phase).toHaveAttribute("data-talk-phase", "listening", { timeout: 10_000 });
    expect(state.spoken.join("")).toContain("回答2です。");

    // 会話中に読み上げを切ると、文字だけで返してすぐ聞き直す（2026-11-19 利用者「会話モードの時にも変えたい」）
    await shell.locator("[data-speaker-toggle]").click();
    const spokenBefore = state.spoken.length;
    transcriptsLeft.push("もう一つ。");
    await tone(page, true);
    await page.waitForTimeout(1700);
    await tone(page, false);
    await expect.poll(() => state.sent.length).toBe(3);
    await expect(shell.getByText("回答3です。").first()).toBeVisible();
    await expect(phase).toHaveAttribute("data-talk-phase", "listening");
    expect(state.spoken.length).toBe(spokenBefore);

    // 停止で終わる（マイクのボタンに戻る）
    await shell.getByRole("button", { name: "会話モードを止める" }).click();
    await expect(phase).toHaveCount(0);
    await expect(shell.getByRole("button", { name: "音声で入力" })).toBeVisible();
  });
}

test("TTS reply mode reads the finished answer and is remembered", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.removeItem("cd-assistant-tts-reply"); });
  const state = await mockChat(page, []);
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  // 読み上げは入力欄の右下のボタンで切り替える（＋の中ではない。2026-11-19 利用者の依頼）
  const speaker = shell.locator("[data-speaker-toggle]");
  await expect(speaker).toHaveAttribute("data-speaker-toggle", "off");
  await speaker.click();
  await expect(speaker).toHaveAttribute("data-speaker-toggle", "on");
  await shell.locator("[data-assistant-input]").fill("こんにちは");
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => state.spoken.join("")).toBe("回答1です。コードは画面に表示しています。");
  expect(state.styles).toEqual([""]);   // 文字で送った発言は会話モードの決まりを付けない
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `${process.env.CONTROL_DECK_E2E_SHOTS ?? "/tmp"}/tts-reply-320.png` });
  await page.reload();
  await expect(shell.locator("[data-speaker-toggle]")).toHaveAttribute("data-speaker-toggle", "on");
});

test("a reply is read once by the tab that sent it, not by another tab watching the same chat", async ({ page, context }) => {
  // 2026-11-20 本番「読み上げが行われるときと行われないときがある」: 1 つの回答に接続が 3〜4 本（別のタブ・購読し直し）。
  // 生成中かどうかの切り替わりで判定していたため、購読し直しで始まった読み上げを止めていた
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.setItem("cd-assistant-tts-reply", "1"); });
  const state = await mockChat(page, []);
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.locator("[data-assistant-input]").fill("こんにちは");
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => state.spoken.length).toBeGreaterThan(0);

  // 裏のタブ: 同じ会話を開くと、生成中の回答を購読し直す。表に出ていないので読まない
  const other = await context.newPage();
  await other.addInitScript(() => Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" }));
  const otherState = await mockChat(other, []);
  otherState.messages.push(
    { id: "u1", role: "user", content: "こんにちは", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-20T00:00:00Z" },
    { id: "a1", role: "assistant", content: "", thinking: "", status: "generating", job_id: "j1", model: "", error: "", meta: {}, created_at: "2026-11-20T00:00:01Z" });
  await other.goto("/assistant");
  await expect(other.locator("[data-assistant-shell]").getByText("回答1です。").first()).toBeVisible();
  await other.waitForTimeout(800);
  expect(otherState.spoken).toEqual([]);
  expect(state.spoken.join("")).toBe("回答1です。コードは画面に表示しています。");   // 送ったタブは 1 回だけ
});


test("a streaming answer is cut at sentence ends and every part is read once", () => {
  // 2026-11-20 利用者「長文だと生成し終えるまで何も再生されずテンポが悪い。途切れないよう順次再生して」
  expect(stablePrefix("一文目です。二文目の途")).toBe("一文目です。");
  expect(stablePrefix("まだ文の途中")).toBe("");
  const parts: string[] = [];
  let queued = "";
  const stream = ["一文目", "一文目です。二", "一文目です。二文目です。\n```py\nprint(", "一文目です。二文目です。\n```py\nprint(1)\n```\n三文目"];
  for (const text of stream) {
    const next = nextSpoken(text, queued, false);
    queued = next.queued;
    if (next.text) parts.push(next.text);
  }
  const last = nextSpoken(stream[stream.length - 1], queued, true);
  parts.push(last.text);
  // 生成中に読んだ分と最後に読んだ分をつなぐと、終わってから全部を読んだときと同じ（重ならず、抜けない）
  expect(parts.join("")).toBe(speakableText(stream[stream.length - 1]));
  expect(parts[0]).toBe("一文目です。");
  // 長い回答は上限で「続きは画面」で終える
  const long = nextSpoken(`${"あ".repeat(800)}。${"い".repeat(800)}。`, "", false);
  expect(long.capped).toBe(true);
  expect(long.text.endsWith("続きは画面をご覧ください。")).toBe(true);
  // 上限でも文の途中では切らない（2026-11-20 実測: 「ため息を続きは画面…」）
  expect(long.text).toBe(`${"あ".repeat(800)}。続きは画面をご覧ください。`);
});

test("TTS reply mode starts reading while the answer is still being written", async ({ page, context }) => {
  // 生成中に文の終わりまで来た分から合成・再生を始める。読み上げのボタンは点滅させない（2026-11-20 利用者「チカチカ」）
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.setItem("cd-assistant-tts-reply", "1"); });
  const state = await mockChat(page, []);
  const timeline: string[] = [];
  await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => undefined);
  const again = await mockChat(page, []);
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", content: "", thinking: "" }));
    setTimeout(() => ws.send(JSON.stringify({ type: "delta", content: "最初の文です。二つ目の" })), 150);
    setTimeout(() => ws.send(JSON.stringify({ type: "delta", content: "文です。" })), 1500);
    setTimeout(() => {
      timeline.push("done");
      ws.send(JSON.stringify({ type: "delta", content: "最後の文です。" }));
      ws.send(JSON.stringify({ type: "done", status: "completed" }));
      void ws.close();
    }, 3000);
  });
  await page.route("**/api/v1/speech/tts", (route) => {
    const text = (route.request().postDataJSON() as { text: string }).text;
    timeline.push(`tts:${text}`);
    again.spoken.push(text);
    return route.fulfill({ body: silentWav(), contentType: "audio/wav" });
  });
  void state;
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.locator("[data-assistant-input]").fill("こんにちは");
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => timeline.includes("done")).toBe(true);
  await expect.poll(() => again.spoken.join("")).toBe("最初の文です。二つ目の文です。最後の文です。");
  expect(timeline[0]).toBe("tts:最初の文です。");             // 生成が終わる前に読み始めている
  expect(timeline.indexOf("tts:最初の文です。")).toBeLessThan(timeline.indexOf("done"));
  const icon = shell.locator("[data-speaker-toggle] svg").first();
  await expect(icon).not.toHaveClass(/animate-pulse/);
});


test("leading and trailing silence of each synthesized piece is cut so pieces join without gaps", () => {
  // 2026-11-20 利用者「長文を連続で読ませると飛び飛びになる」。Supertonic 3 の音声は前後に約 0.4 秒ずつ無音が付き、
  // 1 文ずつ鳴らすと切れ目ごとに約 1 秒途切れていた（実測）。無音を削って前の終わりに続けて予約する
  const rate = 44100;
  const samples = new Float32Array(rate);   // 1 秒: 0.4 秒の無音・0.2 秒の音・0.4 秒の無音
  for (let i = Math.round(rate * 0.4); i < Math.round(rate * 0.6); i++) samples[i] = 0.5 * Math.sin(i / 10) + (i % 2 ? 0.2 : -0.2);
  const [from, to] = voicedRange(samples, rate);
  expect(from / rate).toBeGreaterThan(0.35);
  expect(to / rate).toBeLessThan(0.65);
  expect((to - from) / rate).toBeLessThan(0.3);
  expect(voicedRange(new Float32Array(rate), rate)).toEqual([0, 0]);   // 全部が無音なら鳴らさない
});

test("a long answer is played in order with a sentence-sized pause between pieces", async ({ page, context }) => {
  // 鳴らした音の予約（AudioBufferSourceNode.start）を記録し、前の終わりから次の始まりまでの間を測る
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await page.addInitScript(() => {
    const w = window as unknown as { __starts: { when: number; dur: number }[] };
    w.__starts = [];
    const original = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when?: number, offset?: number, duration?: number) {
      w.__starts.push({ when: when ?? 0, dur: duration ?? this.buffer?.duration ?? 0 });
      return original.call(this, when, offset, duration);
    };
  });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.setItem("cd-assistant-tts-reply", "1"); });
  const state = await mockChat(page, []);
  const sentence = "これは長い回答の文で、順番どおりに途切れず読まれるかを確かめます。";
  const long = Array.from({ length: 24 }, (_, i) => `${i + 1}番目。${sentence}`).join("");
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", content: "", thinking: "" }));
    setTimeout(() => {
      ws.send(JSON.stringify({ type: "delta", content: long }));
      ws.send(JSON.stringify({ type: "done", status: "completed" }));
      void ws.close();
    }, 100);
  });
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.locator("[data-assistant-input]").fill("長く説明して");
  await shell.getByRole("button", { name: "送信" }).click();
  await expect(shell.locator("[data-speaker-toggle]")).toHaveAttribute("data-speaking", "true");
  await expect.poll(() => state.spoken.join("")).toBe(long);                       // 抜けも重なりもなく、順番どおり
  await expect(shell.locator("[data-speaker-toggle]")).toHaveAttribute("data-speaking", "false", { timeout: 15000 });
  const starts = await page.evaluate(() => (window as unknown as { __starts: { when: number; dur: number }[] }).__starts.filter((s) => s.dur > 0.01));
  expect(starts.length).toBe(state.spoken.length);
  for (let i = 1; i < starts.length; i++) {
    const gap = starts[i].when - (starts[i - 1].when + starts[i - 1].dur);
    // 文の間と同じだけ空ける（Supertonic 3 が 1 つの音声の中で空ける 0.3 秒）。前は無音 0.4＋0.4 秒と鳴らし直しで約 1 秒
    expect(gap).toBeGreaterThan(0.25);
    expect(gap).toBeLessThan(0.4);
  }
});

test("after reading aloud, the microphone still opens on an iPhone-like audio session", async ({ page, context }) => {
  // 2026-11-20 本番（iPhone）「AudioSession category is not compatible with audio capture.」: 読み上げのために
  // navigator.audioSession を "playback"（再生専用）にしたまま、音声入力でマイクを開いて失敗した。
  // 偽の audioSession で iPhone と同じ決まり（再生専用ではマイクを開けない）を再現する
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await fakeMicrophone(page);
  await page.addInitScript(() => {
    const w = window as unknown as { __sessionTypes: string[] };
    w.__sessionTypes = [];
    let type = "auto";
    Object.defineProperty(navigator, "audioSession", { configurable: true, value: {
      get type() { return type; },
      set type(value: string) { type = value; w.__sessionTypes.push(value); },
    } });
    const open = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      if (type === "playback") throw new DOMException("AudioSession category is not compatible with audio capture.", "InvalidStateError");
      return open(constraints);
    };
  });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.setItem("cd-assistant-tts-reply", "1"); });
  const state = await mockChat(page, ["音声で入れた文"]);
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.locator("[data-assistant-input]").fill("こんにちは");
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => state.spoken.length).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => (window as unknown as { __sessionTypes: string[] }).__sessionTypes)).toContain("playback");
  await shell.getByRole("button", { name: "音声で入力" }).click();
  await expect(shell.getByRole("button", { name: "音声入力を終える" })).toBeVisible();
  await expect(page.getByText("AudioSession category")).toHaveCount(0);
  await shell.getByRole("button", { name: "音声入力を取り消す" }).click();
  const types = await page.evaluate(() => (window as unknown as { __sessionTypes: string[] }).__sessionTypes);
  expect(types.slice(types.indexOf("playback"))).toEqual(["playback", "play-and-record", "auto"]);   // 閉じたら戻す
});


test("a streamed answer with markdown is read without skipping or reading symbols", () => {
  // 2026-11-20 利用者「多少文言を読み飛ばす」: 文の終わりの記号が太字・コード・リンク・URL の中にあると、そこで切って
  // 読む文の位置がずれ、次の文の頭を飛ばし「**」などを読んでいた（「**結論！** これは」→「**結論！」「れは…」）
  const samples = [
    "**結論！** これは大事です。次に進みます。",
    "`config?.value` を使います。それで終わり。",
    "詳しくは [こちら?](https://x.com/?q=1) を見て。以上です。",
    "*本当に?* はい。そうです！",
    "「精神病院か!?」と叫ぶ。ダニエルは淡々と答えた。",
    "- 項目1です\n- **太字!** の項目\n\n終わり。",
    "## 見出し?\n本文です。続き。",
    "URL https://example.com/a?b=1 を開く。次。",
    "数字は 3.14 です。次は 2.5 倍。",
    "~~古い!~~ 新しい方です。__強調?__ も。",
  ];
  for (const md of samples) {
    let queued = "";
    const parts: string[] = [];
    for (let i = 1; i < md.length; i++) {
      const next = nextSpoken(md.slice(0, i), queued, false);
      queued = next.queued;
      if (next.text) parts.push(next.text);
    }
    parts.push(nextSpoken(md, queued, true).text);
    expect(parts.join(""), md).toBe(speakableText(md));
    expect(parts.join(""), md).not.toMatch(/\*\*|`|\]\(|https?:/);
  }
  // 書式の中の「！」では切らない。閉じた後の文の終わりで切る
  expect(stablePrefix("**結論！** これ")).toBe("");
  expect(stablePrefix("**結論！** これは大事です。次")).toBe("**結論！** これは大事です。");
  // 回した文が食い違っても飛ばさない（食い違った所から読み直す）
  expect(nextSpoken("あいう。えお。", "あいX", true).text).toBe("う。えお。");
});

test("turning reading off mid-answer silences it, and the rest of the stream does not restart it", async ({ page, context }) => {
  // 2026-11-20 本番（iPhone）「読み上げが始まって止めたのに、音が小さくなってまだ鳴り続けた」。生成中から読むので、
  // 止めた後にも続きが届く。予約済みの音は回答ごとの音量の通り道（GainNode）を外してまとめて消し、
  // 止めた回答は続きや終わりが届いても読み直さない
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await page.addInitScript(() => {
    const w = window as unknown as { __gainDisconnects: number };
    w.__gainDisconnects = 0;
    const original = GainNode.prototype.disconnect;
    GainNode.prototype.disconnect = function (...args: unknown[]) {
      w.__gainDisconnects += 1;
      return (original as (...a: unknown[]) => void).apply(this, args);
    } as typeof GainNode.prototype.disconnect;
  });
  await establishSession(page, context);
  await page.evaluate(() => { localStorage.removeItem("cd-chat-conversation"); localStorage.setItem("cd-assistant-tts-reply", "1"); });
  const state = await mockChat(page, []);
  let socket: { send: (data: string) => void } | null = null;
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: "snapshot", content: "", thinking: "" }));
    setTimeout(() => ws.send(JSON.stringify({ type: "delta", content: "最初の文です。".repeat(12) })), 100);
  });
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.locator("[data-assistant-input]").fill("長く話して");
  await shell.getByRole("button", { name: "送信" }).click();
  const speaker = shell.locator("[data-speaker-toggle]");
  await expect(speaker).toHaveAttribute("data-speaking", "true");
  await speaker.click();                                                  // 読み上げを切る
  await expect(speaker).toHaveAttribute("data-speaking", "false");
  expect(await page.evaluate(() => (window as unknown as { __gainDisconnects: number }).__gainDisconnects)).toBeGreaterThan(0);
  await page.waitForTimeout(300);
  const before = state.spoken.length;
  socket!.send(JSON.stringify({ type: "delta", content: "続きの文です。" }));
  socket!.send(JSON.stringify({ type: "done", status: "completed" }));
  await page.waitForTimeout(1500);
  expect(state.spoken.length).toBe(before);                               // 続きも、最初からも読まない
  expect(state.spoken.join("")).not.toContain("続きの文です");
});
