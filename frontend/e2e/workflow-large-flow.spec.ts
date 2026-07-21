import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
}

function definition(size: number, withGroup = false) {
  const nodes = [{ id: "trigger", type: "trigger", name: "Start", config: { mode: "manual" }, position: { x: 40, y: 80 } }];
  const edges: Array<Record<string, unknown>> = [];
  for (let index = 1; index < size; index += 1) {
    const id = `node-${String(index).padStart(3, "0")}`;
    nodes.push({ id, type: "flow.note", name: `Node ${String(index).padStart(3, "0")}`, config: { text: `step ${index}` }, position: { x: 40 + (index % 10) * 220, y: 80 + Math.floor(index / 10) * 110 } });
    edges.push({
      id: `edge-${index}`, source: index === 1 ? "trigger" : `node-${String(index - 1).padStart(3, "0")}`, target: id,
      source_handle: null, target_handle: null, data_type: "object", route: "normal",
    });
  }
  return {
    schema_version: 2,
    settings: { timeout_seconds: 3600, concurrency: 4 },
    nodes,
    edges,
    groups: withGroup ? [{ id: "existing", name: "準備処理", node_ids: ["node-001", "node-002"], collapsed: false }] : [],
  };
}

test("groups, searches, lays out, undoes, and autosaves a 100-node flow", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  test.setTimeout(45_000);
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page);
  runtimeErrors.length = 0;
  const workflowId = await page.evaluate(async (largeDefinition) => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ name: "E2E Large Flow 100", definition: largeDefinition }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).id as number;
  }, definition(100, true));

  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__pane").dispatchEvent("dblclick", { bubbles: true, detail: 2, clientX: 180, clientY: 180 });
    await expect(page.getByRole("dialog", { name: "ノードを追加" })).toBeVisible();
    await page.getByRole("dialog", { name: "ノードを追加" }).getByRole("button", { name: "閉じる" }).click();
    await expect(page.getByRole("button", { name: "フロー内を検索・移動" })).toBeVisible();
    await page.getByRole("button", { name: "フロー内を検索・移動" }).click();
    const outline = page.getByRole("dialog", { name: "フロー内を検索・移動" });
    await expect(outline.getByText("100ノード · 1グループ")).toBeVisible();
    await outline.getByLabel("ノード検索").fill("Node 099");
    await outline.getByRole("button", { name: "Node 099へ移動" }).click();
    await expect(page.locator(".react-flow__node").filter({ hasText: "Node 099" })).toBeVisible();

    await page.getByRole("button", { name: "フロー内を検索・移動" }).click();
    await page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByRole("button", { name: "準備処理を折りたたむ" }).click();
    await page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByRole("button", { name: "閉じる" }).click();
    await expect(page.locator('.react-flow__node[data-id="__group__existing"]')).toContainText("準備処理");
    await expect(page.locator('.react-flow__node[data-id="node-001"]')).toHaveCount(0);

    await page.getByRole("button", { name: "フロー内を検索・移動" }).click();
    await page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByLabel("ノード検索").fill("Node 003");
    await page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByRole("button", { name: "Node 003へ移動" }).click();
    await page.locator('.react-flow__node[data-id="node-003"]').click();
    await page.getByRole("dialog", { name: "注釈" }).getByRole("button", { name: "閉じる" }).click();
    await page.locator('.react-flow__node[data-id="node-004"]').click({ modifiers: ["Control"] });
    await page.getByRole("dialog", { name: "注釈" }).getByRole("button", { name: "閉じる" }).click();
    const groupToolbar = page.getByRole("toolbar", { name: "選択ノードの操作" });
    await expect(groupToolbar).toContainText("2件選択");
    await groupToolbar.getByRole("button", { name: "グループ化" }).click();
    await expect(page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByText("100ノード · 2グループ")).toBeVisible();
    await page.getByRole("dialog", { name: "フロー内を検索・移動" }).getByRole("button", { name: "閉じる" }).click();

    await page.getByRole("button", { name: "ノードを自動配置" }).click();
    await expect(page.getByText("100ノードを自動配置しました")).toBeVisible();
    await page.getByRole("button", { name: "元に戻す" }).click();
    await expect(page.getByRole("button", { name: "やり直す" })).toBeEnabled();
    await page.getByRole("button", { name: "やり直す" }).click();

    await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/workflows/${id}`, { credentials: "same-origin" });
      const body = await response.json();
      return {
        groups: body.definition.groups?.length,
        schema: body.definition.schema_version,
        route: body.definition.edges?.[0]?.route,
        dataType: body.definition.edges?.[0]?.data_type,
      };
    }, workflowId), { timeout: 10_000 }).toEqual({ groups: 2, schema: 2, route: "normal", dataType: "object" });

    for (const viewport of [
      { width: 768, height: 1024 },
      { width: 390, height: 844 },
      { width: 320, height: 700 },
    ]) {
      await page.setViewportSize(viewport);
      await page.getByRole("button", { name: "フロー内を検索・移動" }).click();
      const responsiveOutline = page.getByRole("dialog", { name: "フロー内を検索・移動" });
      await expect(responsiveOutline).toBeVisible();
      const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      await responsiveOutline.getByRole("button", { name: "閉じる" }).click();
    }
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});

test("opens and navigates a 500-node definition without rendering the full canvas", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  test.setTimeout(45_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page);
  const workflowId = await page.evaluate(async (largeDefinition) => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ name: "E2E Large Flow 500", definition: largeDefinition }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).id as number;
  }, definition(500));
  try {
    const started = Date.now();
    await page.goto(`/workflows/${workflowId}`);
    await expect(page.getByText("ナビゲーションモードです", { exact: false })).toBeVisible({ timeout: 15_000 });
    expect(Date.now() - started).toBeLessThan(15_000);
    await expect(page.locator(".react-flow__minimap")).toHaveCount(0);
    expect(await page.locator(".react-flow__node").count()).toBeLessThan(80);
    await expect(page.getByRole("button", { name: "ノードを追加" })).toHaveCount(0);
    await expect(page.getByLabel("ワークフロー名")).toBeDisabled();
    await page.getByRole("button", { name: "フロー内を検索・移動" }).click();
    const outline = page.getByRole("dialog", { name: "フロー内を検索・移動" });
    await expect(outline.getByText("500ノード · 0グループ")).toBeVisible();
    await outline.getByLabel("ノード検索").fill("Node 499");
    await outline.getByRole("button", { name: "Node 499へ移動" }).click();
    await expect(page.locator(".react-flow__node").filter({ hasText: "Node 499" })).toBeVisible();
    expect(await page.locator(".react-flow__node").count()).toBeLessThan(80);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});

test("stops autosave and offers recovery when another editor wins", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page);
  const workflowId = await page.evaluate(async (smallDefinition) => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ name: "E2E Conflict Original", definition: smallDefinition }),
    });
    return (await response.json()).id as number;
  }, definition(3));
  try {
    await page.goto(`/workflows/${workflowId}`);
    await expect(page.getByLabel("ワークフロー名")).toHaveValue("E2E Conflict Original");
    await page.evaluate(async (id) => {
      const current = await fetch(`/api/v1/workflows/${id}`, { credentials: "same-origin" }).then((response) => response.json());
      const response = await fetch(`/api/v1/workflows/${id}`, {
        method: "PATCH", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ name: "E2E Conflict Server", expected_updated_at: current.updated_at }),
      });
      if (!response.ok) throw new Error(await response.text());
    }, workflowId);
    await page.getByLabel("ワークフロー名").fill("E2E Conflict Local");
    const conflict = page.getByRole("alert");
    await expect(conflict).toContainText("別の画面で更新されました", { timeout: 10_000 });
    await expect(conflict.getByRole("button", { name: "手元をJSON保存" })).toBeVisible();
    await conflict.getByRole("button", { name: "最新版を再読み込み" }).click();
    await expect(page.getByLabel("ワークフロー名")).toHaveValue("E2E Conflict Server");
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});
