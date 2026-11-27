// 目的: AI アシスタントのモデルの札が 1 つの選び手で、ふつうのチャットも OpenCode も同じ一覧（種類ごとのまとまり: ローカル・
//   OpenRouter・OpenCode（Zen）・Claude）から選べること。各モデルに一言が付き、細かい値（モデル名）はエキスパートのときだけ出ること。
//   会話で選んだモデルは、OpenCode に切り替えても変わらず（札も送る値も同じ）、会話ごとに戻ること。OpenCode で使えないモデル
//   （チャット専用の Claude）を選んでいたら、切り替えのときに 1 行出して Models の既定へ替えること。
//   Models の「フロンティアモデル」の「OpenCode のモデル」で既定を選んで保存できること。320px・1280px・ライト・ダーク。
// 根拠: 2026-11-27 利用者「AI アシスタントで Qwen で会話を始めたが、OpenCode に切り替えた後は GLM が選択されていた。
//   OpenCode のモデルは Models のフロンティアモデルのところで設定できるようにして。AI アシスタントなどで、他のモデルとローカル LLM を選べるように」。
//   API は page.route で模擬する（OpenCode・LLM・外部のモデルを動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { emptyCloud, mockCloud } from "./support/cloudModels";
import { establishSession, hasSession } from "./support/session";

const CHOICES = {
  base_url: "http://127.0.0.1:8765/api/v1/llm/v1", default_model: "opencode-go/glm-5.3", secret: false,
  groups: [
    { id: "local", label: "ローカル", models: [
      { id: "auto", name: "自動", hint: "Models で選んだ実行環境に任せる（今は Qwen3）", external: false, chat: true, code: true, detail: "" },
      { id: "qwen", name: "Qwen3", hint: "ローカル・外へ出ません・無料", external: false, chat: true, code: true, detail: "qwen" }] },
    { id: "openrouter", label: "OpenRouter", models: [
      { id: "cloud:openrouter/x:free", name: "x:free", hint: "外へ出ます・無料枠", external: true, chat: true, code: true, detail: "cloud:openrouter/x:free" }] },
    { id: "opencode", label: "OpenCode（Zen）", models: [
      { id: "opencode-go/glm-5.3", name: "GLM-5.3", hint: "高性能・OpenCode Go の定額内・外へ出ます・OpenCode 専用", external: true, chat: false, code: true, detail: "opencode-go/glm-5.3" }] },
    { id: "claude", label: "Claude", models: [
      { id: "cloud:anthropic/c", name: "c", hint: "外へ出ます・従量課金・チャット専用", external: true, chat: true, code: false, detail: "cloud:anthropic/c" }] },
  ],
};
const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta, created_at: "2026-11-27T00:00:00Z",
});

async function mock(page: Page, userMeta: object) {
  const sent: Record<string, unknown>[] = [];
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/workflows\/llm-endpoints$/, (route) => route.fulfill({ json: [{
    base_url: CHOICES.base_url, managed: true, selected: true, selected_runtime: "strata", models: ["auto", "qwen"], model_labels: { qwen: "Qwen3" },
    auto_resolves_to: { alias: "qwen", name: "Qwen3", runtime: "strata", loaded: true },
  }] }));
  await page.route(/\/api\/v1\/workflows\/model-choices/, (route) => route.fulfill({ json: CHOICES }));
  await page.route(/\/api\/v1\/opencode\/workflow$/, (route) => route.fulfill({ json: { role_info: { error: "", notes: [], effective: {} } } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/m1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "m1", title: "Qwen の会話" },
    messages: [msg("u0", "user", "こんにちは", { mode_choice: "chat", ...userMeta }), msg("a0", "assistant", "こんにちは。")],
  } }));
  // 1 回目の回答が終わらない間の 2 回目は送信待ち（queue）へ入る。どちらも同じ選び方が付く
  await page.route(/\/api\/v1\/chat\/conversations\/m1\/queue$/, (route) => {
    if (route.request().method() === "POST") {
      sent.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ status: 201, json: { id: "q1", status: "queued" } });
    }
    return route.fulfill({ json: { items: [], generating_message_id: null, held: false } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/m1\/send$/, (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 201, json: { user_message_id: "u1", assistant_message_id: "a1", job_id: "j1", status: "generating" } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, () => undefined);
  return sent;
}

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`model picker: grouped list, same model across modes, per conversation (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await page.evaluate(() => { localStorage.setItem("cd-chat-conversation", "m1"); localStorage.setItem("control-deck:commander-expert", "0"); });
      const sent = await mock(page, { model_choice: "qwen" });
      await page.goto("/assistant");
      const shell = page.locator("[data-assistant-shell]");
      await expect(shell.getByText("こんにちは。")).toBeVisible();

      // 札: 会話に保存した Qwen（再読み込み・別の端末でも同じ）
      const pill = shell.locator("[data-model-pill]");
      await expect(pill).toContainText("qwen");
      await pill.click();
      const picker = shell.locator("[data-model-picker]");
      await expect(picker.locator("select")).toHaveValue("qwen");
      // 種類ごとのまとまり。使えない経路（Codex）は出さない
      const labels = await picker.locator("optgroup").evaluateAll((els) => els.map((el) => el.getAttribute("label")));
      expect(labels).toEqual(["ローカル", "OpenRouter", "OpenCode（Zen）", "Claude"]);
      // 各モデルに一言。細かい値はエキスパートだけ
      await expect(picker.locator('[data-model-option="qwen"]')).toHaveText(/Qwen3 — ローカル・外へ出ません・無料/);
      await expect(picker.locator("[data-model-hint]")).toContainText("外へ出ません");
      await expect(picker.locator("[data-model-detail]")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/model-picker-${width}-${scheme}.png` });
      await page.evaluate(() => localStorage.setItem("control-deck:commander-expert", "1"));
      await page.reload();
      await shell.locator("[data-model-pill]").click();
      await expect(shell.locator("[data-model-detail]")).toContainText("qwen");
      await page.evaluate(() => localStorage.setItem("control-deck:commander-expert", "0"));

      // OpenCode に切り替えても、Qwen のまま（札も送る値も）
      await page.reload();
      await shell.locator("[data-assistant-plus]").click();
      await page.getByRole("menu", { name: "添付・機能・権限" }).locator('[data-mode-option="code"]').click();
      await expect(shell.locator("[data-model-pill]")).toContainText("qwen");
      await shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）").selectOption("app");
      await shell.locator("textarea").fill("直して");
      await shell.locator("textarea").press("Enter");
      await expect.poll(() => sent.length).toBeGreaterThan(0);
      expect(sent[0]).toMatchObject({ mode: "code", model: "qwen", code_model: "qwen", model_explicit: true, model_choice: "qwen" });

      // OpenCode の中で別のモデル（OpenCode 専用の GLM）を選ぶ。チャット側のモデルは Qwen のまま
      await shell.locator("[data-model-pill]").click();
      await shell.locator("[data-model-picker] select").selectOption("opencode-go/glm-5.3");
      await expect(shell.locator("[data-model-pill]")).toContainText("GLM-5.3");
      await shell.locator("textarea").fill("もう一度");
      await shell.locator("textarea").press("Enter");
      await expect.poll(() => sent.length).toBeGreaterThan(1);
      expect(sent[1]).toMatchObject({ mode: "code", model: "qwen", code_model: "opencode-go/glm-5.3" });

      // OpenCode で使えない（チャット専用の）モデルを選ぶと、1 行出して Models の既定（GLM-5.3）へ
      await shell.locator("[data-model-picker] select").selectOption("cloud:anthropic/c");
      await expect(page.getByText("OpenCode ではcが使えないのでGLM-5.3にします")).toBeVisible();
      await expect(shell.locator("[data-model-pill]")).toContainText("GLM-5.3");
    });
  }
}

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`Models frontier: OpenCode model is chosen and saved here (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await mockCloud(page, emptyCloud());
      const puts: Record<string, unknown>[] = [];
      let current = "";
      await page.route(/\/api\/v1\/opencode\/default-model$/, (route) => {
        if (route.request().method() === "PUT") {
          const body = route.request().postDataJSON() as { default_model: string };
          puts.push(body);
          current = body.default_model;
        }
        return route.fulfill({ json: { default_model: current } });
      });
      await page.route(/\/api\/v1\/workflows\/model-choices/, (route) => route.fulfill({ json: CHOICES }));
      await page.goto("/models?tab=frontier");
      const panel = page.locator("[data-opencode-model]");
      await expect(panel).toBeVisible();
      const select = panel.getByLabel("OpenCode のモデル");
      // OpenCode で使えるものだけ（チャット専用の Claude は並ばない）
      const labels = await select.locator("optgroup").evaluateAll((els) => els.map((el) => el.getAttribute("label")));
      expect(labels).toEqual(["ローカル", "OpenRouter", "OpenCode（Zen）"]);
      await select.selectOption("opencode-go/glm-5.3");
      await expect.poll(() => puts.length).toBe(1);
      expect(puts[0]).toEqual({ default_model: "opencode-go/glm-5.3" });
      await select.selectOption("controldeck/qwen");
      await expect.poll(() => puts.length).toBe(2);
      expect(puts[1]).toEqual({ default_model: "controldeck/qwen" });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      const box = await select.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      await page.screenshot({ path: `test-results/models-opencode-model-${width}-${scheme}.png` });
    });
  }
}
