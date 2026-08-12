import { api } from "./client";

export interface FlowAppField {
  name: string;
  label: string;
  type: string;
  required: boolean;
  control: string;
  enum?: string[];
  default?: unknown;
  description?: string;
}

export interface FlowAppDiagnostic {
  code: string;
  severity: "error" | "warning" | "suggestion";
  message: string;
  path?: string;
  suggestedFix?: string;
}

export type FlowAppFormat = "pyz" | "binary";

export interface FlowAppFormatOption {
  id: FlowAppFormat;
  label: string;
  available: boolean;
  requires: string;
  size: string;
  buildTime: string;
  note: string;
}

export interface FlowAppExport {
  filename: string;
  format: FlowAppFormat;
  size: number;
  createdAt: string;
  checksum: string;
  name: string;
  nodeCount: number;
  inputs: FlowAppField[];
  outputs: FlowAppField[];
  requires: string;
  runHint?: string;
}

export interface FlowAppPreview {
  workflowId: number;
  name: string;
  description: string;
  portable: boolean;
  diagnostics: FlowAppDiagnostic[];
  blockedNodeTypes: string[];
  nodeTypes: Record<string, number>;
  inputs: FlowAppField[];
  outputs: FlowAppField[];
  exports: FlowAppExport[];
}

export interface FlowAppCapability {
  available: boolean;
  formats: FlowAppFormatOption[];
  supportedNodes: string[];
}

export const flowAppApi = {
  capability: () => api<FlowAppCapability>("/flow-apps/capability"),
  preview: (workflowId: number) => api<FlowAppPreview>(`/flow-apps/${workflowId}/preview`),
  exports: (workflowId: number) => api<FlowAppExport[]>(`/flow-apps/${workflowId}/exports`),
  create: (workflowId: number, format: FlowAppFormat = "pyz") =>
    api<FlowAppExport & { job_id?: string }>(`/flow-apps/${workflowId}/exports`, {
      method: "POST", json: { format },
    }),
  job: (jobId: string) =>
    api<{ status: string; error: string; progress?: { status?: string } }>(`/jobs/${jobId}`),
  remove: (workflowId: number, filename: string) =>
    api<void>(`/flow-apps/${workflowId}/exports/${encodeURIComponent(filename)}`, { method: "DELETE" }),
  downloadUrl: (workflowId: number, filename: string) =>
    `/api/v1/flow-apps/${workflowId}/exports/${encodeURIComponent(filename)}/download`,
};
