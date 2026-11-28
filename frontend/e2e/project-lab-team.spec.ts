// 目的: Project Lab で、プロジェクトが Nexarch の AI チームの作業場所なら、見出しに「AI チーム」の札が出て、情報の枠に
//       チームの状態・変更ファイル（＋−の行数）・ファイルごとの差分・仕事の一覧が出ること。
// 根拠: 2026-11-28 利用者「チームの作業場所を設定したと思うんだけど、プロジェクトラボでその成果物とか見れないんでしょうか。
//       今後チームを使うことがあると思うので見れるようにしてほしい」。API は模擬する。320・1280 で横にはみ出さない。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const folder = "NewsHub-measurement";
const summary = {
  id: folder, name: folder, description: "", createdBy: "nexarch", modifiedAt: "2026-11-28T12:00:00+00:00", technologies: [],
  git: { branch: "main", dirty: false }, diagnostics: [], capabilities: {}, artifactCount: 0, profileCount: 0,
};
const team = {
  id: "t331774e2", name: "NewsHub v0.9 測定の記録（C2・C3）", leader_status: "running", goal: "C2 と C3", summary: "",
  landed: false, base_branch: "main", team_branch: "nexarch/t331774e2",
  tasks: [{ id: 1, title: "測定の表と移行", status: "done", tier: "implement", model: "HY4 Flash（無料）", branch: "nexarch/t331774e2-1" }],
  files: [{ path: "backend/newshub/measure_vocab.py", added: 120, removed: 0 }, { path: "backend/newshub/db.py", added: 30, removed: 2 }],
  added: 150, removed: 2,
};

for (const [width, height] of [[320, 760], [1280, 900]] as const) {
  test(`project lab: AI チームの成果（変更ファイル・差分・仕事）を見る（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/project-lab\/settings$/, (r) => r.fulfill({ json: { allow_external_preview: false, project_root: "/CodeDEV" } }));
    await page.route(/\/api\/v1\/project-lab\/projects$/, (r) => r.fulfill({ json: [summary] }));
    await page.route(/\/api\/v1\/project-lab\/runs/, (r) => r.fulfill({ json: [] }));
    await page.route(new RegExp(`/api/v1/project-lab/projects/${folder}$`), (r) =>
      r.fulfill({ json: { ...summary, path: `/CodeDEV/${folder}`, manifest: null, artifacts: [] } }));
    await page.route(new RegExp(`/api/v1/project-lab/projects/${folder}/team$`), (r) => r.fulfill({ json: { team } }));
    await page.route(new RegExp(`/api/v1/project-lab/projects/${folder}/team/diff`), (r) => r.fulfill({ json: {
      path: "backend/newshub/db.py", truncated: false,
      diff: "diff --git a/backend/newshub/db.py b/backend/newshub/db.py\n@@ -1,2 +1,3 @@\n-old = 1\n+new = 2\n+add = 3\n" } }));
    await page.goto("/project-lab");

    await page.locator("[data-team-chip]").click();
    const info = page.getByRole("dialog", { name: "プロジェクト情報" });
    const section = info.locator(`[data-project-team="${team.id}"]`);
    await expect(section).toContainText("AI チームの成果: NewsHub v0.9 測定の記録（C2・C3）");
    await expect(section).toContainText("進行中");
    await expect(section).toContainText("+150 −2");
    await section.getByRole("button", { name: /backend\/newshub\/db\.py/ }).click();
    await expect(section.locator("[data-team-diff]")).toContainText("+new = 2");
    await section.getByText("仕事 1 件").click();
    await expect(section).toContainText("測定の表と移行");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.screenshot({ path: `test-results/project-lab-team-${width}.png` });
  });
}
