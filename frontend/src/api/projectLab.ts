import { api } from "./client";

export interface ProjectLabArtifact {
  path: string;
  name: string;
  kind: "html" | "image" | "table" | "json" | "markdown" | "pdf" | "audio" | "video" | "log" | "text" | "code";
  language: string;
  runnable: boolean;
  /** HTMLが外部CDN等を参照しているか（プレビューは既定で遮断する）。 */
  external?: boolean;
  mimeType: string;
  size: number;
  modifiedAt: string;
  previewText: string | null;
  structuredPreview: unknown;
}

export interface ProjectLabSummary {
  id: string;
  name: string;
  description: string;
  modifiedAt: string;
  technologies: string[];
  git: { branch: string; dirty: boolean | null } | null;
  diagnostics: Array<{ code: string; severity: string; message: string }>;
  capabilities: Record<string, boolean>;
  artifactCount: number;
  profileCount: number;
}

export interface ProjectLabDetail extends Omit<ProjectLabSummary, "artifactCount" | "profileCount"> {
  path: string;
  manifest: null | {
    schemaVersion: 1;
    name: string;
    description: string;
    profiles: Array<{ id: string; label: string; type: string; command: string[]; cwd: string; environmentNames: string[]; secretRefs: string[]; artifacts: string[] }>;
  };
  artifacts: ProjectLabArtifact[];
}

export interface ProjectLabRunArtifact {
  id: number;
  path: string;
  kind: string;
  mimeType: string;
  size: number;
  checksum: string;
  changeType: "created" | "modified";
}

export interface ProjectLabRun {
  id: number;
  projectId: string;
  projectName: string;
  profileId: string;
  profileType: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELED" | "TIMED_OUT" | "INTERRUPTED";
  command: string[];
  environmentNames: string[];
  timeoutSeconds: number;
  result: string;
  exitCode: number | null;
  error: string;
  startedAt: string;
  finishedAt: string | null;
  elapsedMs: number | null;
  previewUrl: string | null;
  previewReady: boolean;
  artifacts: ProjectLabRunArtifact[];
}

/** ZIPの中身の下見。excluded は落とした理由まで持つ——落ちた事実だけ見せても、
 *  利用者は「入っているはずの file が無い」としか分からない。 */
export interface ProjectLabExportPlan {
  projectId: string;
  fileCount: number;
  totalBytes: number;
  excluded: { path: string; reason: string }[];
  excludedTruncated: boolean;
}

/** 公開の下見。公開は取り消しても索引に残るので、押す前に必ずこれを見せる。 */
export interface ProjectLabPublishPlan {
  projectId: string;
  directory: string;
  candidates: { directory: string; hasIndex: boolean }[];
  hasIndex: boolean;
  fileCount: number;
  totalBytes: number;
  excluded: { path: string; reason: string }[];
  excludedTruncated: boolean;
  github: { available: boolean; loggedIn: boolean; account: string };
  current: ProjectLabPublishState | null;
}

export interface ProjectLabPublishState {
  repository: string;
  visibility: "public" | "private";
  branch: string;
  directory: string;
  url: string;
  fileCount: number;
  excludedCount: number;
}

export interface ProjectLabSettings {
  allow_external_preview: boolean;
  /** プロジェクトの置き場。設定で変わるので、案内文にパスを埋め込まない。 */
  project_root?: string;
}

export const projectLabApi = {
  settings: () => api<ProjectLabSettings>("/project-lab/settings"),
  saveSettings: (patch: Partial<ProjectLabSettings>) =>
    api<ProjectLabSettings>("/project-lab/settings", { method: "PUT", json: patch }),
  list: () => api<ProjectLabSummary[]>("/project-lab/projects"),
  detail: (id: string) => api<ProjectLabDetail>(`/project-lab/projects/${encodeURIComponent(id)}`),
  runs: (id: string) => api<ProjectLabRun[]>(`/project-lab/runs?project_id=${encodeURIComponent(id)}`),
  startRun: (id: string, profileId: string, timeoutSeconds = 600) => api<ProjectLabRun>(`/project-lab/projects/${encodeURIComponent(id)}/runs`, {
    method: "POST", json: { profile_id: profileId, timeout_seconds: timeoutSeconds },
  }),
  startFileRun: (id: string, path: string, timeoutSeconds = 300) => api<ProjectLabRun>(`/project-lab/projects/${encodeURIComponent(id)}/file-runs`, {
    method: "POST", json: { path, timeout_seconds: timeoutSeconds },
  }),
  cancelRun: (runId: number) => api<ProjectLabRun>(`/project-lab/runs/${runId}/cancel`, { method: "POST" }),
  runLogs: (runId: number) => api<{ runId: number; logs: string }>(`/project-lab/runs/${runId}/logs`),
  preview: (id: string, path: string) => api<Pick<ProjectLabArtifact, "path" | "previewText" | "structuredPreview">>(
    `/project-lab/projects/${encodeURIComponent(id)}/previews/${path.split("/").map(encodeURIComponent).join("/")}`,
  ),
  artifactUrl: (id: string, path: string, options: { download?: boolean; external?: boolean } = {}) => {
    const query = [options.download ? "download=true" : "", options.external ? "external=true" : ""].filter(Boolean).join("&");
    return `/api/v1/project-lab/projects/${encodeURIComponent(id)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}${query ? `?${query}` : ""}`;
  },
  /** ZIPに何が入り、何が落ちるか。ダウンロードの前に見せるためのもの。 */
  exportPlan: (id: string) => api<ProjectLabExportPlan>(
    `/project-lab/projects/${encodeURIComponent(id)}/export-plan`,
  ),
  /** ZIP本体。ブラウザに直接取りに行かせるので URL だけ返す。 */
  archiveUrl: (id: string) => `/api/v1/project-lab/projects/${encodeURIComponent(id)}/archive`,
  /** 公開の下見。directory を変えると候補ごとの内訳が返る。 */
  publishPlan: (id: string, directory?: string) => api<ProjectLabPublishPlan>(
    `/project-lab/projects/${encodeURIComponent(id)}/publish-plan${
      directory === undefined ? "" : `?directory=${encodeURIComponent(directory)}`}`,
  ),
  /** 公開を取り下げる。リポジトリ自体は残る。 */
  unpublish: (id: string) => api<{
    repository: string; branch: string; removed: string[];
    pagesSettingRemains: boolean;
    repositoryRemains: boolean; repositoryUrl: string;
  }>(`/project-lab/projects/${encodeURIComponent(id)}/publish`, { method: "DELETE" }),
  publish: (id: string, body: { repository: string; visibility: "public" | "private"; directory: string | null }) =>
    api<ProjectLabPublishState>(`/project-lab/projects/${encodeURIComponent(id)}/publish`, {
      method: "POST", json: body,
    }),
  /** iframeプレビュー用の短命token。sandboxの不透明originからはcookieが送れないため。 */
  previewToken: (id: string) => api<{ token: string; expires_in: number }>(
    `/project-lab/projects/${encodeURIComponent(id)}/preview-token`, { method: "POST" },
  ),
  /** tokenをパスに含めるので、HTMLからの相対参照にもそのまま引き継がれる。 */
  previewUrl: (token: string, path: string, options: { external?: boolean } = {}) => {
    const query = options.external ? "?external=true" : "";
    return `/api/v1/project-lab/preview/${encodeURIComponent(token)}/${path.split("/").map(encodeURIComponent).join("/")}${query}`;
  },
};
