import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("public runner edits and resumes a durable approval on mobile", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  const workflowId = await page.evaluate(async (assignee) => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const definition = {
      nodes: [
        { id: "t", type: "trigger", config: { mode: "manual" } },
        { id: "gate", type: "human.approval", config: {
          message: "内容を確認して必要なら修正してください", approver: assignee,
          approval_timeout_seconds: 120,
          form_schema: { type: "object", properties: { comment: { type: "string", title: "修正コメント" } }, required: ["comment"] },
        } },
        { id: "out", type: "output.render", config: { name: "result", title: "結果", renderer: "plain_text", value: "{{gate.response.comment}}" } },
      ],
      edges: [{ source: "t", target: "gate" }, { source: "gate", target: "out" }],
    };
    const created = await fetch("/api/v1/workflows", { method: "POST", credentials: "same-origin", headers, body: JSON.stringify({ name: "E2E Durable Pause UI", definition }) });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers });
    if (!published.ok) throw new Error(await published.text());
    return id;
  }, username!);

  try {
    await page.goto(`/runner?workflow=${workflowId}`);
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("承認が必要です")).toBeVisible();
    await page.getByLabel("修正コメント").fill("GUIから修正して続行");
    await page.getByRole("button", { name: "承認", exact: true }).click();
    await expect(page.getByText("GUIから修正して続行", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});

test("public runner submits a durable typed human form", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  const workflowId = await page.evaluate(async (assignee) => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const definition = {
      nodes: [
        { id: "t", type: "trigger", config: { mode: "manual" } },
        { id: "form", type: "human.form", config: {
          message: "公開情報を入力してください", approver: assignee, form_timeout_seconds: 120,
          inputs: [
            { key: "title", label: "公開タイトル", type: "text", required: true, maxLength: 40 },
            { key: "priority", label: "優先度", type: "select", required: true, options: "low,high" },
            { key: "notify", label: "通知する", type: "boolean" },
          ],
        } },
        { id: "out", type: "flow.return", config: { name: "result", title: "結果", renderer: "plain_text", value: "{{form.response.title}}/{{form.response.priority}}/{{form.response.notify}}" } },
      ],
      edges: [{ source: "t", target: "form" }, { source: "form", target: "out" }],
    };
    const created = await fetch("/api/v1/workflows", { method: "POST", credentials: "same-origin", headers, body: JSON.stringify({ name: "E2E Durable Human Form", definition }) });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers });
    if (!published.ok) throw new Error(await published.text());
    return id;
  }, username!);

  try {
    await page.goto(`/runner?workflow=${workflowId}`);
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("入力が必要です")).toBeVisible();
    const submit = page.getByRole("button", { name: "送信", exact: true });
    await expect(submit).toBeDisabled();
    await page.getByLabel("公開タイトル *").fill("release");
    await page.getByLabel("優先度 *").selectOption("high");
    await page.getByLabel("通知する").check();
    await expect(submit).toBeEnabled();
    await submit.click();
    await expect(page.getByText("release/high/true", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.getByText("release/high/true", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__node").filter({ hasText: "入力フォーム" }).click();
    await expect(page.locator("#node-config-form-message")).toHaveValue("公開情報を入力してください");
    await expect(page.getByRole("button", { name: "入力フィールドを追加" })).toBeVisible();
    await expect(page.getByPlaceholder("変数名")).toHaveCount(3);
    await page.setViewportSize({ width: 320, height: 720 });
    await expect(page.locator("#node-config-form-message")).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});
