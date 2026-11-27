// 目的: Project Lab のプロジェクト情報で、表示名をその場で書き換えられる（✏️ 名前を変える）。
//       自動で作られた作業場所には「どこから作られたか」の小さな印（Nexarch・チーム・AI アシスタント）が出る。
// 根拠: 2026-11-27 利用者「作業場所は既定の作業場所に自動で名前を振ってもらって、後で書き換えられるようにし、
//       そこを Project Lab からも見られればよくないか？」。API は模擬する。320・1280 で横にはみ出さない。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

function summary(id: string, name: string, createdBy: string | null) {
  return {
    id, name, description: "", createdBy, modifiedAt: "2026-11-27T06:00:00+00:00", technologies: [],
    git: { branch: "main", dirty: false }, diagnostics: [], capabilities: {}, artifactCount: 0, profileCount: 0,
  };
}

for (const [width, height] of [[320, 700], [1280, 900]] as const) {
  test(`project lab: 表示名を書き換えられ、作成元の印が出る（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    const state = { name: "新しい作業場所 11/27 15:30", puts: [] as Array<{ id: string; name: string }> };
    const folder = "nexarch-20261127-153000";
    await page.route(/\/api\/v1\/project-lab\/settings$/, (route) =>
      route.fulfill({ json: { allow_external_preview: false, project_root: "/CodeDEV" } }));
    await page.route(/\/api\/v1\/project-lab\/projects$/, (route) =>
      route.fulfill({ json: [summary(folder, state.name, "nexarch")] }));
    await page.route(/\/api\/v1\/project-lab\/runs/, (route) => route.fulfill({ json: [] }));
    await page.route(new RegExp(`/api/v1/project-lab/projects/${folder}/name$`), (route) => {
      const body = route.request().postDataJSON() as { name: string };
      state.puts.push({ id: folder, name: body.name });
      state.name = body.name;
      return route.fulfill({ json: { id: folder, name: body.name } });
    });
    await page.route(new RegExp(`/api/v1/project-lab/projects/${folder}$`), (route) =>
      route.fulfill({ json: { ...summary(folder, state.name, "nexarch"), path: `/CodeDEV/${folder}`, manifest: null, artifacts: [] } }));
    await page.goto("/project-lab");

    await page.getByRole("button", { name: "プロジェクト情報" }).click();
    const info = page.getByRole("dialog", { name: "プロジェクト情報" });
    await expect(info.getByTestId("project-name")).toHaveText("新しい作業場所 11/27 15:30");
    await expect(info.getByTestId("created-by")).toHaveText("Nexarch");
    await info.getByRole("button", { name: "名前を変える" }).click();
    const input = info.getByLabel("プロジェクトの表示名");
    await input.fill("夜の調査");
    await page.screenshot({ path: `test-results/project-lab-rename-${width}.png` });
    await info.getByRole("button", { name: "保存" }).click();
    await expect(info.getByTestId("project-name")).toHaveText("夜の調査");
    expect(state.puts).toEqual([{ id: folder, name: "夜の調査" }]);
    // フォルダ名（id）は変わらない
    await expect(info.getByText(`/CodeDEV/${folder}`)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  });
}
