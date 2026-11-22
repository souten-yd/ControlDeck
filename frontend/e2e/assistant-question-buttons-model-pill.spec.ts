// 目的: (U13) OpenCode の回の本文に「決めてもらいたいこと」の質問があれば、本文の下に答えの画面（選択肢のラジオ／チェックボックス・
//       「推奨」の札・推奨は最初から選択・「その他」の自由入力・全体の補足）が出て、「この答えで作る」はビルドで、
//       「答えてプランを直す」はプランのまま、答えを付けて送ること。答えの無い質問があれば押せないこと。
//       質問があるプランでは、答え無しの単独「このプランで作る」を出さないこと。
//       抜き出しは、新しい書き方・古い書き方（→ 推奨：○○）・複数可・選択肢の無い質問・見出しの無い本文を扱い、LLM は使わない。
//       (U11) ヘッダの札が「auto（実際のモデル名）」になり、320px でも札が切れて崩れず（長ければ省略・title に全体）、
//       OpenCode の会話では設定に「役割ごとのモデル」の一覧が出ること。
// 根拠: 2026-11-23 利用者の通し（評価 U13・U11）。「Claude みたいにチェックボックスと自由入力で答えたい。選択肢にも推奨が出てほしい。
//       推奨どおりに進めると『このプランで作る』が同時に出ていて、答え無しで始めてよいのか分からない」。
//       API は page.route で模擬する（OpenCode と LLM を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { answerOf, buildPlanAnswer, extractPlanQuestions, initialPicks } from "../src/features/workflows/planQuestions";
import { establishSession, hasSession } from "./support/session";

const BODY = "計画です。\n\n## 決めてもらいたいこと\n\n1. ＋の隣の札もアイコンに揃えますか？\n   - 揃える（推奨）— 見た目が統一される\n   - 今のまま\n2. 出す項目（複数可）\n   - 名前（推奨）\n   - 日付\n   - 場所（推奨）\n3. 新しい札の名前は？\n\n## 次\n\n別の話。";
const NEW = [
  { n: 1, question: "＋の隣の札もアイコンに揃えますか？", multi: false, options: [
    { label: "揃える", recommended: true, note: "見た目が統一される" }, { label: "今のまま", recommended: false, note: "" }] },
  { n: 2, question: "出す項目", multi: true, options: [
    { label: "名前", recommended: true, note: "" }, { label: "日付", recommended: false, note: "" }, { label: "場所", recommended: true, note: "" }] },
  { n: 3, question: "新しい札の名前は？", multi: false, options: [] },
];

test("extractPlanQuestions: new format / old format / multi / free-only / no heading", () => {
  const found = extractPlanQuestions(BODY);
  expect(found).toEqual(NEW);
  const picks = initialPicks(found);
  expect(picks).toEqual([{ picked: [0], other: "" }, { picked: [0, 2], other: "" }, { picked: [], other: "" }]);   // 推奨は最初から選ぶ。自由入力だけは空
  expect(answerOf(found[0], picks[0])).toBe("揃える（推奨どおり）");
  expect(answerOf(found[1], { picked: [2, 1], other: "" })).toBe("日付、場所");                                     // 推奨どおりでなければ付けない
  expect(answerOf(found[1], { picked: [1], other: " 全部 " })).toBe("日付、全部");                                  // 複数可では自由入力を選んだものに足す
  expect(answerOf(found[1], { picked: [], other: "全部" })).toBe("全部");
  expect(answerOf(found[0], { picked: [0], other: "半分だけ" })).toBe("半分だけ");                                   // 1 つだけ選ぶ質問では自由入力が選択の代わり
  expect(buildPlanAnswer(found, [picks[0], picks[1], { picked: [], other: "tag" }], "急がない", false)).toBe(
    "決めたこと:\n1. ＋の隣の札もアイコンに揃えますか？ → 揃える（推奨どおり）\n2. 出す項目 → 名前、場所（推奨どおり）\n3. 新しい札の名前は？ → tag\n補足: 急がない\nこの答えでプランどおり実装してください。");
  expect(buildPlanAnswer(found.slice(2), [{ picked: [], other: "x" }], "", true)).toMatch(/この答えでプランを直してください。$/);

  // 古い書き方: 推奨 1 つの選択肢になる。推奨の無い質問があっても全体を捨てない
  const old = extractPlanQuestions("## 決めてもらいたいこと\n1. 揃えますか？ → **推奨：揃える**（見た目が統一されるため）\n2. 小ラベルを残しますか？\n   - 推奨: 残す。スマホで迷わない\n3. 名前は？");
  expect(old.map((q) => [q.n, q.question, q.options])).toEqual([
    [1, "揃えますか？", [{ label: "揃える", recommended: true, note: "" }]],
    [2, "小ラベルを残しますか？", [{ label: "残す", recommended: true, note: "" }]],
    [3, "名前は？", []],
  ]);
  expect(extractPlanQuestions("決めてもらいたいこと\n\n無し")).toEqual([]);
  expect(extractPlanQuestions("1. どうしますか？ 推奨：A\n   - はい（推奨）")).toEqual([]);                         // 見出しが無ければ出さない
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
      await expect(page.locator("[data-build-from-plan]")).toHaveCount(0);   // 質問があるときは答え無しの単独ボタンを出さない
      await expect(box.locator("[data-plan-recommended-badge]")).toHaveCount(3);
      await expect(box.locator('[data-plan-question="1"] input:checked')).toHaveCount(1);
      await expect(box.locator('[data-plan-question="2"] input:checked')).toHaveCount(2);
      await expect(box.locator('[data-plan-question="2"] input[type=checkbox]')).toHaveCount(3);
      await expect(box.locator('[data-plan-question="1"] input[type=radio]')).toHaveCount(2);
      const optionBox = await box.locator("[data-plan-option]").first().boundingBox();
      expect(optionBox!.height).toBeGreaterThanOrEqual(44);
      await expect(box.locator("[data-proceed-recommended]")).toBeDisabled();   // 3 番に答えが無い
      await expect(box.locator("[data-plan-missing]")).toContainText("3");
      expect(await box.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0);
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

      await box.locator('[data-plan-question="3"] [data-plan-other]').fill("tag");
      await box.locator('[data-plan-question="1"] [data-plan-option]').nth(1).click();   // 選択を変えると文が変わる
      await box.locator('[data-plan-question="2"] [data-plan-option]').nth(1).click();   // 日付を足す
      await box.locator("[data-plan-extra]").fill("急がない");
      await box.locator("[data-answer-replan]").click();   // プランのまま答えだけ送る
      await expect.poll(() => sent.length).toBe(1);
      const text = "決めたこと:\n1. ＋の隣の札もアイコンに揃えますか？ → 今のまま\n2. 出す項目 → 名前、日付、場所\n3. 新しい札の名前は？ → tag\n補足: 急がない\n";
      expect(sent[0]).toMatchObject({ mode: "code", code_agent: "plan", content: `${text}この答えでプランを直してください。` });
      await expect(box).toHaveCount(0);   // 送ったら閉じる
    });
  }
}

async function open(page: Page, context: Parameters<typeof establishSession>[1], body: string, agent = "plan") {
  await establishSession(page, context);
  const sent = await mock(page, body);
  if (agent !== "plan") {
    await page.route(/\/api\/v1\/chat\/conversations\/q1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "q1", title: "質問" },
      messages: [msg("u0", "user", "作って"), msg("a0", "assistant", body, { mode: "code", project_path: "/CodeDEV/app", agent })],
    } }));
  }
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "q1"));
  await page.goto("/assistant");
  return sent;
}

test("the primary button builds with the answers, a free answer replaces the choice, old-style recommendation still works", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  const sent = await open(page, context, "計画です。\n\n## 決めてもらいたいこと\n\n1. 揃えますか？ → 推奨：揃える\n2. 色は？\n   - 青\n   - 赤（推奨）");
  const box = page.locator("[data-plan-recommend]");
  await expect(box.locator('[data-plan-question="1"] input:checked')).toHaveCount(1);   // 古い書き方の推奨も最初から選ばれる
  await expect(box.locator("[data-proceed-recommended]")).toBeEnabled();
  await box.locator('[data-plan-question="1"] [data-plan-other]').fill("全部やめる");
  await box.locator('[data-plan-question="2"] [data-plan-option]').first().click();
  await box.locator("[data-proceed-recommended]").click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_agent: "build",
    content: "決めたこと:\n1. 揃えますか？ → 全部やめる\n2. 色は？ → 青\nこの答えでプランどおり実装してください。" });
  await expect(page.locator("[data-assistant-input-card]")).toHaveAttribute("data-agent", "build");
});

test("the recommended answers are sent as they are, and in a build turn only one button is shown", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  const sent = await open(page, context, BODY.replace("3. 新しい札の名前は？", "3. 色は？\n   - 青（推奨）"), "build");
  const box = page.locator("[data-plan-recommend]");
  await expect(box.locator("[data-answer-replan]")).toHaveCount(0);
  await expect(box.locator("[data-proceed-recommended]")).toContainText("この答えで進める");
  await box.locator("[data-proceed-recommended]").click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_agent: "build",
    content: "決めたこと:\n1. ＋の隣の札もアイコンに揃えますか？ → 揃える（推奨どおり）\n2. 出す項目 → 名前、場所（推奨どおり）\n3. 色は？ → 青（推奨どおり）\nこの答えでプランどおり実装してください。" });
});

test("no answer form when there are no questions, and the plain build button stays", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await open(page, context, "計画です。\n\n## 決めてもらいたいこと\n\n無し");
  await expect(page.locator("[data-assistant-shell]")).toContainText("計画です。");
  await expect(page.locator("[data-plan-recommend]")).toHaveCount(0);
  await expect(page.locator("[data-build-from-plan]")).toBeVisible();
});
