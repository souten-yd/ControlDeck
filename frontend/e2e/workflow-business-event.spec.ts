import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("publishes a durable workflow event and receives its typed payload at desktop and 320px", async ({ page }) => {
  test.setTimeout(45_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const created = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const eventName = `e2e.completed.${Date.now()}`;
    const create = async (name: string, definition: unknown) => {
      const response = await fetch("/api/v1/workflows", {
        method: "POST", credentials: "same-origin", headers,
        body: JSON.stringify({ name, definition }),
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).id as number;
    };
    const receiver = await create("E2E Business Event Receiver", {
      nodes: [
        { id: "trigger", type: "trigger", name: "業務イベント受信", config: {
          mode: "event", event_source: "workflow", event_name: eventName,
        }, position: { x: 40, y: 150 } },
        { id: "result", type: "flow.return", name: "受信結果", config: {
          name: "received", title: "受信結果", renderer: "plain_text",
          value: "received={{trigger.data.message}}; event={{trigger.event_name}}",
        }, position: { x: 360, y: 150 } },
      ],
      edges: [{ source: "trigger", target: "result" }],
    });
    const sender = await create("E2E Business Event Sender", {
      nodes: [
        { id: "trigger", type: "trigger", name: "入力", config: {
          mode: "manual", inputs: [{ key: "message", label: "通知メッセージ", type: "text", required: true }],
        }, position: { x: 40, y: 150 } },
        { id: "emit", type: "event.emit", name: "イベント発行", config: {
          event_name: eventName,
          payload: { message: "{{trigger.message}}", nested: { source: "runner" } },
        }, position: { x: 340, y: 150 } },
        { id: "result", type: "flow.return", name: "配送結果", config: {
          name: "delivery", title: "配送結果", renderer: "plain_text",
          value: "status={{emit.status}}; delivered={{emit.delivered_count}}; event={{emit.event_name}}",
        }, position: { x: 640, y: 150 } },
      ],
      edges: [{ source: "trigger", target: "emit" }, { source: "emit", target: "result" }],
    });
    for (const id of [receiver, sender]) {
      const published = await fetch(`/api/v1/workflows/${id}/publish`, {
        method: "POST", credentials: "same-origin", headers,
      });
      if (!published.ok) throw new Error(await published.text());
    }
    const enabled = await fetch(`/api/v1/workflows/${receiver}/enable`, {
      method: "POST", credentials: "same-origin", headers,
    });
    if (!enabled.ok) throw new Error(await enabled.text());
    return { receiver, sender, eventName };
  });

  try {
    await page.goto(`/workflows/${created.receiver}`);
    await page.locator(".react-flow__node").filter({ hasText: "業務イベント受信" }).click();
    await expect(page.locator("#node-config-trigger-mode")).toHaveValue("event");
    await expect(page.locator("#node-config-trigger-event_source")).toHaveValue("workflow");
    await expect(page.locator("#node-config-trigger-event_name")).toHaveValue(created.eventName);
    await expect(page.locator("#node-config-trigger-rule_filter")).toHaveCount(0);

    await page.goto(`/workflows/${created.sender}`);
    await page.locator(".react-flow__node").filter({ hasText: "イベント発行" }).click();
    await expect(page.locator("#node-config-emit-event_name")).toHaveValue(created.eventName);
    await expect(page.locator("#node-config-emit-payload")).toHaveValue(/trigger\.message/);

    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(`/runner?workflow=${created.sender}`);
    await page.getByLabel("通知メッセージ").fill("mobile-ready");
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText(`status=DISPATCHED; delivered=1; event=${created.eventName}`, { exact: true }))
      .toBeVisible({ timeout: 15_000 });

    const received = await page.evaluate(async ({ receiver, eventName }) => {
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const list = await fetch(`/api/v1/workflow-executions?workflow_id=${receiver}&limit=1`, { credentials: "same-origin" });
        const rows = await list.json();
        if (rows[0]?.status === "SUCCEEDED" && rows[0]?.trigger_type === "event:workflow") {
          const detail = await fetch(`/api/v1/workflow-executions/${rows[0].id}`, { credentials: "same-origin" });
          const execution = await detail.json();
          if (execution.context?.trigger?.output?.event_name === eventName) return execution;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      throw new Error("business event receiver did not finish");
    }, created);
    expect(received.context.trigger.output.data.message).toBe("mobile-ready");
    expect(received.context.trigger.output.data.nested.source).toBe("runner");
    expect(received.context.result.output.value).toBe(`received=mobile-ready; event=${created.eventName}`);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async ({ sender, receiver }) => {
      for (const id of [sender, receiver]) {
        await fetch(`/api/v1/workflows/${id}`, {
          method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
        });
      }
    }, created);
  }
});
