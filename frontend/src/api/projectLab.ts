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

export const projectLabApi = {
  list: () => api<ProjectLabSummary[]>("/project-lab/projects"),
  detail: (id: string) => api<ProjectLabDetail>(`/project-lab/projects/${encodeURIComponent(id)}`),
  preview: (id: string, path: string) => api<Pick<ProjectLabArtifact, "path" | "previewText" | "structuredPreview">>(
    `/project-lab/projects/${encodeURIComponent(id)}/previews/${path.split("/").map(encodeURIComponent).join("/")}`,
  ),
  artifactUrl: (id: string, path: string, download = false) =>
    `/api/v1/project-lab/projects/${encodeURIComponent(id)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}${download ? "?download=true" : ""}`,
};
