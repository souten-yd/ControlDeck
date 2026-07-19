import { api } from "./client";

export interface ProjectLabArtifact {
  path: string;
  name: string;
  kind: "html" | "image" | "table" | "json" | "markdown" | "pdf" | "audio" | "video" | "log" | "text";
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

export const projectLabApi = {
  list: () => api<ProjectLabSummary[]>("/project-lab/projects"),
  detail: (id: string) => api<ProjectLabDetail>(`/project-lab/projects/${encodeURIComponent(id)}`),
  runs: (id: string) => api<ProjectLabRun[]>(`/project-lab/runs?project_id=${encodeURIComponent(id)}`),
  startRun: (id: string, profileId: string, timeoutSeconds = 600) => api<ProjectLabRun>(`/project-lab/projects/${encodeURIComponent(id)}/runs`, {
    method: "POST", json: { profile_id: profileId, timeout_seconds: timeoutSeconds },
  }),
  cancelRun: (runId: number) => api<ProjectLabRun>(`/project-lab/runs/${runId}/cancel`, { method: "POST" }),
  runLogs: (runId: number) => api<{ runId: number; logs: string }>(`/project-lab/runs/${runId}/logs`),
  preview: (id: string, path: string) => api<Pick<ProjectLabArtifact, "path" | "previewText" | "structuredPreview">>(
    `/project-lab/projects/${encodeURIComponent(id)}/previews/${path.split("/").map(encodeURIComponent).join("/")}`,
  ),
  artifactUrl: (id: string, path: string, download = false) =>
    `/api/v1/project-lab/projects/${encodeURIComponent(id)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=true" : ""}`,
};
