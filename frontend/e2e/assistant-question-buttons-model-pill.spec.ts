// 目的: (U13) OpenCode の回の本文に「決めてもらいたいこと」の番号付きの質問（推奨つき）があれば、本文の下に推奨の一覧と
//       「推奨どおりで進める」が出て、押すと「推奨どおりでお願いします（1: …、2: …）」を同じ会話・同じモードへ送ること。
//       見つからない・推奨の無い質問があるときは何も出さないこと（検出は本文の書き方だけ。LLM は使わない）。
//       (U11) ヘッダの札が「auto（実際のモデル名）」になり、320px でも札が切れて崩れず（長ければ省略・title に全体）、
//       OpenCode の会話では設定に「役割ごとのモデル」の一覧が出ること。
// 根拠: 2026-11-23 利用者の通し（評価 U13・U11）。推奨つきの質問に「両方推奨どおり」と打っていた／「auto」では何のモデルが動くか分からなかった。
//       API は page.route で模擬する（OpenCode と LLM を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { extractPlanQuestions, recommendedAnswer } from "../src/features/workflows/planQuestions";
import { establishSession, hasSession } from "./support/session";

const BODY = "計画です。\n\n## 決めてもらいたいこと\n\n1. ＋の隣の札もアイコンに揃えますか？ → **推奨：揃える**（見た目が統一されるため）\n2. 小ラベルを残しますか？\n   - 推奨: 残す。スマホで迷わない\n\n## 次\n\n別の話。";

test("extractPlanQuestions: found / none / recommendation text / all-or-nothing", () => {
  const found = extractPlanQuestions(BODY);
  expect(found).toEqual([
    { n: 1, question: "＋の隣の札もアイコンに揃えますか？", recommended: "揃える" },
    { n: 2, question: "小ラベルを残しますか？", recommended: "残す" },
  ]);
  expect(recommendedAnswer(found)).toBe("推奨どおりでお願いします（1: 揃える、2: 残す）");
  expect(extractPlanQuestions("決めてもらいたいこと\n\n無し")).toEqual([]);
  expect(extractPlanQuestions("1. どうしますか？ 推奨：A")).toEqual([]);                                // 見出しが無ければ出さない
  expect(extractPlanQuestions("## 決めてもらいたいこと\n1. A？ 推奨：x\n2. B？")).toEqual([]);          // 推奨の無い質問があれば出さない
});

const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta, created_at: "2026-11-23T00:00:00Z",
});

async function mock(page: Page, body: string) {
  const sent: Record<string, unknown>[] = [];
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/workflows\/llm-endpoints$/, (route) => route.fulfill({ json: [{
    base_url: "http://127.0.0.1:8765/v1", managed: true, selected: true, selected_runtime: "strata",
    models: ["auto", "qwen-flash-next-very-long-model-name"], model_labels: {},
    auto_resolves_to: { alias: "qwen-flash-next-very-long-model-name", name: "qwen-flash-next-very-long-model-name", runtime: "strata", loaded: true },
  }] }));
  await page.route(/\/api\/v1\/opencode\/workflow$/, (route) => route.fulfill({ json: { role_info: { error: "", notes: [], effective: {
    plan: { model: "local/qwen-flash-next", explicit: false, stage: 1 }, build: { model: "openrouter/glm-5.3", explicit: true, stage: 2 },
  } } } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/q1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "q1", title: "質問" },
    messages: [msg("u0", "user", "計画して"), msg("a0", "assistant", body, { mode: "code", project_path: "/CodeDEV/app", agent: "plan" })],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/q1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/chat\/conversations\/q1\/send$/, (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 201, json: { user_message_id: "u1", assistant_message_id: "a1", job_id: "j1", status: "generating" } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, () => undefined);
  return sent;
}

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`proceed-with-recommendations button and model pill (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      const sent = await mock(page, BODY);
      await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "q1"));
      await page.goto("/assistant");

      const box = page.locator("[data-plan-recommend]");
      await expect(box).toContainText("揃える");
      await expect(box).toContainText("残す");
      const pill = page.locator("[data-model-pill]");
      await expect(pill).toHaveAttribute("title", /auto（qwen-flash-next-very-long-model-name）/);
      await expect(pill).toContainText("auto（");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      const pillBox = await pill.boundingBox();
      expect(pillBox!.x + pillBox!.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: `test-results/question-buttons-${width}-${scheme}.png` });

      // 役割ごとのモデル（設定を開くと OpenCode の会話では一覧が出る）
      await pill.click();
      await page.locator("[data-role-models] summary").click();
      await expect(page.locator("[data-role-models]")).toContainText("開発");
      await expect(page.locator("[data-role-models]")).toContainText("openrouter/glm-5.3");
      await expect(page.locator("[data-role-models]")).toContainText("（既定）");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/model-pill-roles-${width}-${scheme}.png` });
      await pill.click();

      await page.locator("[data-proceed-recommended]").click();
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0]).toMatchObject({ mode: "code", code_agent: "plan", content: "推奨どおりでお願いします（1: 揃える、2: 残す）" });
    });
  }
}

test("no buttons when the body has no recommended questions", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await mock(page, "計画です。\n\n## 決めてもらいたいこと\n\n無し");
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "q1"));
  await page.goto("/assistant");
  await expect(page.locator("[data-assistant-shell]")).toContainText("計画です。");
  await expect(page.locator("[data-plan-recommend]")).toHaveCount(0);
});
