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
  propertySchema: ComponentPropertyDefinition[];
  eventSchema: ComponentEventDefinition[];
  accessibility?: { requiredProperties?: string[]; minimumTouchTarget?: number };
}

export interface ComponentEventDefinition {
  name: string;
  label: string;
  actions: string[];
}

export interface ComponentPropertyDefinition {
  key: string;
  label: string;
  type: "string" | "multiline" | "boolean" | "number" | "enum" | "json" | "responsive-columns" | "table-columns" | "chart-series";
  required?: boolean;
  options?: string[];
  minimum?: number;
  maximum?: number;
  breakpoints?: string[];
  maximumItems?: number;
  columnTypes?: string[];
  tones?: string[];
}

export interface DesignPresetDefinition {
  id: string; label: string; description: string; tokens: Record<string, string>;
}

export interface DesignTemplateDefinition {
  id: string; label: string; description: string; category?: string; root: SemanticComponent;
  parameters: DesignTemplateParameterDefinition[];
}

export interface DesignTemplateParameterDefinition {
  key: string;
  label: string;
  type: "string" | "number" | "boolean" | "enum";
  default: string | number | boolean;
  required?: boolean;
  maximumLength?: number;
  minimum?: number;
  maximum?: number;
  options?: string[];
  targets: Array<{ componentId: string; property: string }>;
}

export interface SemanticComponentCatalog {
  schemaVersion: number;
  components: ComponentDefinition[];
  designTokens: Record<string, string[]>;
  bindingSources: string[];
  bindingDefinitions: Array<{ id: string; label: string; referenceLabel: string }>;
  eventActions: Array<{ id: string; label: string; targetLabel: string; targetSection: string | null }>;
  accessibilityAudit: { minimumContrast: number; minimumLargeTextContrast: number; minimumTouchTarget: number; minimumFocusIndicator: number };
  presets: DesignPresetDefinition[];
  composites: DesignTemplateDefinition[];
  patterns: DesignTemplateDefinition[];
  previewStates: Array<{ id: "default" | "loading" | "empty" | "error" | "disabled"; label: string; description: string }>;
}

export interface ApplicationSchemaCatalog {
  schemaVersion: number;
  semanticComponents: SemanticComponentCatalog;
}

export type ApplicationEntityFieldType = "string" | "integer" | "number" | "boolean" | "datetime" | "json";
export interface ApplicationEntityField {
  id: string; type: ApplicationEntityFieldType; nullable?: boolean; default?: unknown; hasDefault?: boolean;
  maxLength?: number | null; unique?: boolean; indexed?: boolean;
  reference?: { entityId: string; onDelete: "restrict" | "cascade" | "set-null" } | null;
}
export interface ApplicationEntity {
  id: string; displayName?: string; tableName?: string | null; fields: ApplicationEntityField[];
  crud?: { enabled: boolean; operations: Array<"create" | "read" | "list" | "update" | "delete">; basePath?: string | null };
}

export type ApplicationClientStateType = "string" | "integer" | "number" | "boolean" | "object" | "array";
export interface ApplicationClientState {
  id: string;
  type: ApplicationClientStateType;
  initialValue: unknown;
  nullable?: boolean;
}

export interface ApplicationQuery {
  id: string;
  source: "entity" | "api";
  entityId?: string | null;
  endpointId?: string | null;
  input?: Record<string, unknown>;
  resultPath?: string;
  filters?: Array<{ field: string; operator: "eq" | "ne" | "contains" | "starts-with" | "gt" | "gte" | "lt" | "lte" | "is-null"; value?: unknown }>;
  sort?: Array<{ field: string; direction: "asc" | "desc" }>;
  pagination?: "none" | "offset";
  limit: number;
  autoLoad: boolean;
  cachePolicy: "network-only" | "memory";
  staleTimeSeconds: number;
}

export interface ApplicationPatchOperation {
  op: "add" | "remove" | "replace" | "move";
  path: string;
  from?: string;
  value?: unknown;
}

export interface ApplicationPatchPreview {
  valid: boolean;
  baseChecksum: string;
  resultChecksum: string;
  patchedSpec: Record<string, unknown>;
  appliedPatches: ApplicationPatchOperation[];
  diagnostics: Diagnostic[];
}

export interface ApplicationDesignProposal {
  id: string;
  direction: "simple" | "balanced" | "dense";
  title: string;
  summary: string;
  rationale: string[];
  patches: ApplicationPatchOperation[];
  warnings: string[];
  preview: ApplicationPatchPreview;
}

export interface LlmEndpoint {
  base_url: string;
  models: string[];
  managed?: boolean;
  selected?: boolean;
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
  details: { sdks: string[]; features: Record<string, boolean> };
  matrix: PlatformCapabilityMatrix;
}

export interface PlatformCapabilityMatrix {
  spec: string; source: string; localBuild: string; remoteBuild: string;
  package: string; signing: string; store: string; stability: string;
}

export interface CapabilityCatalog {
  phase: string;
  generationAvailable: boolean;
  buildAvailable: boolean;
  build: { available: boolean; sdk: string; sdkPath: string | null; systemdUser: boolean; network: "denied"; maxConcurrent: number; note: string };
  frameworks: FrameworkCapability[];
  nodes: Array<{ type: string; targets: Record<string, { support: string; reason: string }> }>;
  host: { os: string; architecture: string; sdks: Record<string, boolean>; note: string };
}

export interface PlatformAdvisorRequest {
  platforms: Array<"web" | "linux" | "windows" | "macos" | "android" | "ios">;
  offline: boolean; localFiles: boolean; tray: boolean; background: boolean; gpu: boolean;
  embeddedServer: boolean; store: boolean; preferredLanguage: "any" | "csharp" | "typescript" | "rust" | "dart" | "kotlin" | "cpp";
  preferNativeFeel: boolean; preferWebReuse: boolean; preferSmallSize: boolean;
}

export interface PlatformRecommendation {
  frameworkId: string; label: string; score: number; platforms: string[]; language: string; status: string;
  reasons: string[]; constraints: string[]; matrix: PlatformCapabilityMatrix;
}

export interface PlatformAdvisorResult {
  phase: string; recommendedId: string; requestedPlatforms: string[];
  recommendations: PlatformRecommendation[]; host: CapabilityCatalog["host"]; note: string;
}

export interface ApplicationPreflightResult {
  phase: string; validSpec: boolean; readyForGeneration: boolean; readyForLocalBuild: boolean;
  targets: Array<{ id: string; frameworkId: string; label: string; platforms: string[]; matrix: PlatformCapabilityMatrix; requiredSdks: string[]; missingSdks: string[] }>;
  diagnostics: Diagnostic[]; host: CapabilityCatalog["host"];
  sideEffects: { executor: false; network: false; subprocess: false; filesystemWrite: false; secretResolution: false };
}

export interface ApplicationSourcePreview {
  phase: string; ready: boolean; diagnostics: Diagnostic[];
  sideEffects: { executor: false; network: false; subprocess: false; filesystemWrite: false; secretResolution: false };
  generator?: { id: string; version: string }; deterministic?: boolean;
  archiveName?: string; archiveChecksum?: string; sourceChecksum?: string; archiveBytes?: number;
  files?: Array<{ path: string; sha256: string; bytes: number; kind: "managed" | "extension" | "config" | "manifest" }>;
  manifest?: { input: { specChecksum: string; workflowChecksum: string; targetId: string; framework: string }; managedFiles: string[]; extensionFiles: string[]; configFiles: string[] };
}

export interface ValidationResult {
  valid: boolean;
  normalizedSpec: Record<string, unknown>;
  workflowIr: null | { inputs: unknown[]; outputs: unknown[]; nodes: unknown[]; edges: unknown[]; capabilities: string[]; side_effects: string[] };
  applicationIr: { pages: unknown[]; entities: unknown[]; api_endpoints: unknown[]; targets: unknown[] };
  diagnostics: Diagnostic[];
  capability: { target: string; generationAvailable: boolean; buildAvailable: boolean; note: string };
}

export interface ApplicationBuildArtifact {
  id: number; path: string; kind: "source" | "binary" | string; mimeType: string; size: number; checksum: string;
}

export interface ApplicationBuild {
  id: number; projectId: number; targetId: string; framework: string;
  status: "queued" | "preparing" | "generating" | "restoring" | "building" | "testing" | "canceling" | "completed" | "failed" | "cancelled" | "timed_out" | "interrupted";
  sourceChecksum: string; archiveChecksum: string; generator: { id?: string; version?: string }; sdk: string;
  timeoutSeconds: number; result: string; exitCode: number | null; error: string;
  createdAt: string; startedAt: string | null; finishedAt: string | null;
  isolation: { systemdUser: true; network: "denied"; memoryMax: string; tasksMax: number; cpuQuota: string };
  artifacts: ApplicationBuildArtifact[]; logs?: string;
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
  advisePlatforms: (body: PlatformAdvisorRequest) => api<PlatformAdvisorResult>("/application-builder/platform-advisor", { method: "POST", json: body }),
  preflight: (project: ApplicationProject, spec: Record<string, unknown>) => api<ApplicationPreflightResult>("/application-builder/preflight", {
    method: "POST", json: { spec, workflow_id: project.workflow_id },
  }),
  sourcePreview: (projectId: number, targetId: string) => api<ApplicationSourcePreview>(`/application-projects/${projectId}/source-preview?target_id=${encodeURIComponent(targetId)}`),
  downloadSource: async (projectId: number, targetId: string) => {
    const response = await fetch(`/api/v1/application-projects/${projectId}/source-archive`, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ targetId }),
    });
    if (!response.ok) {
      let message = `Source生成に失敗しました (${response.status})`;
      try {
        const payload = await response.json() as { detail?: string | { diagnostics?: Diagnostic[] } };
        if (typeof payload.detail === "string") message = payload.detail;
        else if (payload.detail?.diagnostics?.length) message = payload.detail.diagnostics.map((item) => `${item.code}: ${item.message}`).join(" / ");
      } catch { /* non-JSON error */ }
      throw new Error(message);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? "generated-source.zip";
    return { blob: await response.blob(), filename, checksum: response.headers.get("X-ControlDeck-Source-SHA256") ?? "" };
  },
  builds: (projectId: number) => api<ApplicationBuild[]>(`/application-projects/${projectId}/builds`),
  startBuild: (projectId: number, targetId: string, timeoutSeconds = 900) =>
    api<ApplicationBuild>(`/application-projects/${projectId}/builds`, { method: "POST", json: { targetId, timeoutSeconds } }),
  buildLogs: (buildId: number) => api<ApplicationBuild>(`/application-builds/${buildId}/logs`),
  cancelBuild: (buildId: number) => api<ApplicationBuild>(`/application-builds/${buildId}/cancel`, { method: "POST" }),
  removeBuild: (buildId: number) => api<void>(`/application-builds/${buildId}`, { method: "DELETE" }),
  artifactUrl: (buildId: number, artifactId: number) => `/api/v1/application-builds/${buildId}/artifacts/${artifactId}`,
  schema: () => api<ApplicationSchemaCatalog>("/application-builder/schema"),
  validateSpec: (project: ApplicationProject, spec: Record<string, unknown>) => api<ValidationResult>("/application-builder/validate", {
    method: "POST", json: { spec, workflow_id: project.workflow_id, target: project.target },
  }),
  validate: (project: ApplicationProject) => api<ValidationResult>("/application-builder/validate", {
    method: "POST", json: { spec: project.spec, workflow_id: project.workflow_id, target: project.target },
  }),
  previewPatches: (spec: Record<string, unknown>, patches: ApplicationPatchOperation[]) =>
    api<ApplicationPatchPreview>("/application-builder/patches/preview", { method: "POST", json: { spec, patches } }),
  applyPatches: (projectId: number, baseChecksum: string, patches: ApplicationPatchOperation[]) =>
    api<{ project: ApplicationProject; patch: ApplicationPatchPreview }>(`/application-projects/${projectId}/patches/apply`, {
      method: "POST", json: { base_checksum: baseChecksum, patches },
    }),
  llmEndpoints: () => api<LlmEndpoint[]>("/workflows/llm-endpoints"),
  designProposals: (projectId: number, body: {
    instruction: string; scope: "application" | "page" | "component" | "mobile";
    target_id?: string; mode: "preserve" | "balanced" | "redesign"; base_url: string; model: string;
  }) => api<{ proposals: ApplicationDesignProposal[] }>(`/application-projects/${projectId}/design-proposals`, { method: "POST", json: body }),
};
