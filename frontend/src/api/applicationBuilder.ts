import { api } from "./client";

export interface Diagnostic {
  code: string;
  severity: "error" | "warning" | "suggestion";
  message: string;
  path: string;
  source: string;
  suggestedFix: string;
  autoFix: boolean;
}

export interface ApplicationProject {
  id: number;
  name: string;
  description: string;
  workflow_id: number | null;
  spec: Record<string, unknown>;
  schema_version: number;
  target: string;
  application_type: string;
  ui_framework: string;
  status: "draft" | "archived";
  created_at: string;
  updated_at: string;
}

export interface SemanticComponent {
  id: string;
  type: string;
  properties?: Record<string, unknown>;
  binding?: string | Record<string, unknown> | null;
  events?: Record<string, unknown>;
  responsive?: Record<string, unknown>;
  locked?: Record<string, boolean>;
  children?: SemanticComponent[];
}

export interface ComponentDefinition {
  type: string; label: string; category: string; container: boolean; defaults: Record<string, unknown>;
}

export interface ApplicationSchemaCatalog {
  schemaVersion: number;
  semanticComponents: {
    schemaVersion: number;
    components: ComponentDefinition[];
    designTokens: Record<string, string[]>;
    bindingSources: string[];
  };
}

export interface FrameworkCapability {
  id: string;
  label: string;
  language: string;
  platforms: string[];
  status: string;
  source: boolean;
  build: boolean;
  package: boolean;
  phase: string;
}

export interface CapabilityCatalog {
  phase: string;
  generationAvailable: false;
  buildAvailable: false;
  frameworks: FrameworkCapability[];
  nodes: Array<{ type: string; targets: Record<string, { support: string; reason: string }> }>;
  host: { os: string; architecture: string; sdks: Record<string, boolean>; note: string };
}

export interface ValidationResult {
  valid: boolean;
  normalizedSpec: Record<string, unknown>;
  workflowIr: null | { inputs: unknown[]; outputs: unknown[]; nodes: unknown[]; edges: unknown[]; capabilities: string[]; side_effects: string[] };
  applicationIr: { pages: unknown[]; entities: unknown[]; api_endpoints: unknown[]; targets: unknown[] };
  diagnostics: Diagnostic[];
  capability: { target: string; generationAvailable: false; buildAvailable: false; note: string };
}

export const applicationBuilderApi = {
  list: (workflowId?: number) => api<ApplicationProject[]>(`/application-projects${workflowId ? `?workflow_id=${workflowId}` : ""}`),
  get: (id: number) => api<ApplicationProject>(`/application-projects/${id}`),
  create: (body: { name: string; description?: string; workflow_id?: number }) =>
    api<ApplicationProject>("/application-projects", { method: "POST", json: body }),
  createFromWorkflow: (workflowId: number, body: { source: "draft" | "published"; name?: string }) =>
    api<ApplicationProject>(`/workflows/${workflowId}/application-projects`, { method: "POST", json: body }),
  update: (id: number, body: { name?: string; description?: string; spec?: Record<string, unknown> }) =>
    api<ApplicationProject>(`/application-projects/${id}`, { method: "PATCH", json: body }),
  remove: (id: number) => api<void>(`/application-projects/${id}`, { method: "DELETE" }),
  capabilities: () => api<CapabilityCatalog>("/application-builder/capabilities"),
  schema: () => api<ApplicationSchemaCatalog>("/application-builder/schema"),
  validateSpec: (project: ApplicationProject, spec: Record<string, unknown>) => api<ValidationResult>("/application-builder/validate", {
    method: "POST", json: { spec, workflow_id: project.workflow_id, target: project.target },
  }),
  validate: (project: ApplicationProject) => api<ValidationResult>("/application-builder/validate", {
    method: "POST", json: { spec: project.spec, workflow_id: project.workflow_id, target: project.target },
  }),
};
