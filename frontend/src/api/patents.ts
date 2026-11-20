/** Patent（特許調査・検討）API の型。backend/app/patents/router.py と対になる。 */
import { api } from "./client";

export interface Stage { id: string; label: string; goal: string; ai: string[] }
export interface Deadline { key: string; label: string; due: string; basis: string; days_left: number }
export interface CaseSummary {
  id: number; title: string; stage: string; stage_label: string; created: number; updated: number;
  next_deadline: Deadline | null;
}
export interface PatentEvent { id: number; kind: string; date: string; note: string }
export interface ClaimVersion { id: number; version: number; text: string; note: string; created: number }
export interface PatentRef { id: number; doc_ref: string; ref: string; category: string; relevance: string; note: string }
export interface ClaimElement { label: string; text: string }
export interface Assist {
  id: number; task: string; label: string; status: string; answer: string; error: string;
  evidence: { doc_ref: string; title: string; collection: string }[];
  basis: string; review_status: string; job_id: string; created: number;
}
export interface CaseDetail extends CaseSummary {
  data: Record<string, string | boolean>;
  events: PatentEvent[];
  claims: ClaimVersion[];
  refs: PatentRef[];
  assists: Assist[];
  deadlines: Deadline[];
  stage_info: Stage | null;
  claim_elements: { no: number; text: string; elements: ClaimElement[] }[];
}
export interface PatentMeta {
  stages: Stage[];
  event_kinds: Record<string, string>;
  ref_categories: Record<string, string>;
  tasks: Record<string, string>;
  local_ai_available: boolean;
  literature_collection: string;
}
export interface SearchHit {
  doc_ref: string; chunk_ref: string; collection: string; title: string; source: string; url: string;
  score: number; snippet: string; sensitivity: string; relation?: string;
}
export interface ClaimChart {
  claim_no: number; claim: string;
  rows: { element: ClaimElement; query: string; evidence: { doc_ref: string; title: string; collection: string; snippet: string }[] }[];
}

export type Verdict = "○" | "△" | "×" | "?";
export interface JudgeRow {
  label: string; element: string; model_verdict: Verdict; verdict: Verdict; quote: string; where: string; reason: string;
  quote_verified: boolean; flag: string; human_verdict: Verdict | ""; human_note: string;
}
export interface JudgedChart {
  id: number; claim_no: number; claim_version: number; doc_ref: string; doc_title: string; status: string; rows: JudgeRow[];
  context_mode: string; error: string; review_status: string; basis: string; stale?: string[]; created: number;
}

export interface CountRow { key: string; count: number }
export interface PatentAnalytics {
  documents: number;
  coverage: { year: number; applicant: number; ipc: number; claims: number };
  by_year: CountRow[];
  applicants: CountRow[];
  ipc_subclasses: CountRow[];
  countries: CountRow[];
  kinds: CountRow[];
  applicant_ipc: { applicant: string; ipc: string; count: number }[];
  applicant_year: { applicant: string; year: string; count: number }[];
  claims_histogram: CountRow[];
  note: string;
}
export interface MapPoint { ref: string; title: string; x: number; y: number; applicant: string; ipc: string; year: string }
export interface PatentMap { points: MapPoint[]; model: string; explained: number[] }
/** 公報の書誌（knowledge/biblio.py の形）。source は読み取り方（manual / ocr_extract / rule_extract など）。 */
export interface Biblio {
  title?: string; applicants?: string[]; inventors?: string[]; ipc?: string[];
  filing_date?: string; publication_date?: string; priority_date?: string;
  country?: string; kind?: string; claims_count?: number | null; source?: string;
}
export interface SimilarHit {
  ref: string; title: string; score: number; snippet: string; applicants: string[]; ipc: string[]; date: string; registered: boolean;
}

export interface PatentSource { id: string; label: string; kind: string; hint: string; signup: string; ready: boolean; keys: string[] }
export interface Watch {
  id: number; source: string; source_label: string; query: string; interval_hours: number; enabled: boolean;
  approved_public: boolean; last_run: number | null; last_status: string;
}
export interface WatchHit { id: number; watch_id: number; doc_ref: string; title: string; score: number; status: string; created: number }
export interface WatchBody { source: string; query: string; interval_hours: number; approved_public: boolean; enabled: boolean }

export const patentsApi = {
  meta: () => api<PatentMeta>("/patents/meta"),
  list: () => api<CaseSummary[]>("/patents/cases"),
  get: (id: number) => api<CaseDetail>(`/patents/cases/${id}`),
  create: (title: string) => api<CaseDetail>("/patents/cases", { method: "POST", json: { title } }),
  patch: (id: number, body: { title?: string; stage?: string; data?: Record<string, unknown> }) =>
    api<CaseDetail>(`/patents/cases/${id}`, { method: "PATCH", json: body }),
  remove: (id: number) => api(`/patents/cases/${id}`, { method: "DELETE" }),
  addEvent: (id: number, body: { kind: string; date: string; note: string }) =>
    api<CaseDetail>(`/patents/cases/${id}/events`, { method: "POST", json: body }),
  removeEvent: (id: number, eventId: number) => api<CaseDetail>(`/patents/cases/${id}/events/${eventId}`, { method: "DELETE" }),
  saveClaims: (id: number, text: string) => api<CaseDetail>(`/patents/cases/${id}/claims`, { method: "POST", json: { text } }),
  appendHearing: (id: number, text: string, replace = false) =>
    api<CaseDetail>(`/patents/cases/${id}/hearing`, { method: "POST", json: { text, replace } }),
  addRef: (id: number, body: { ref?: string; doc_ref?: string; category: string; relevance?: string }) =>
    api<CaseDetail>(`/patents/cases/${id}/refs`, { method: "POST", json: body }),
  removeRef: (id: number, refId: number) => api<CaseDetail>(`/patents/cases/${id}/refs/${refId}`, { method: "DELETE" }),
  search: (query: string) => api<{ results: SearchHit[] }>("/patents/search", { method: "POST", json: { query, limit: 20 } }),
  claimChart: (id: number, claimNo: number) =>
    api<ClaimChart>(`/patents/cases/${id}/claim-chart`, { method: "POST", json: { claim_no: claimNo } }),
  charts: (id: number) => api<{ charts: JudgedChart[] }>(`/patents/cases/${id}/charts`),
  judge: (id: number, claimNo: number, docRef: string) =>
    api<{ chart_id: number; job_id: string }>(`/patents/cases/${id}/charts`, { method: "POST", json: { claim_no: claimNo, doc_ref: docRef } }),
  setJudgeRow: (id: number, chartId: number, label: string, verdict: string, note = "") =>
    api<JudgedChart>(`/patents/cases/${id}/charts/${chartId}/rows/${encodeURIComponent(label)}`, { method: "PATCH", json: { verdict, note } }),
  reviewChart: (id: number, chartId: number, review_status: string) =>
    api<JudgedChart>(`/patents/cases/${id}/charts/${chartId}/review`, { method: "POST", json: { review_status } }),
  removeChart: (id: number, chartId: number) => api(`/patents/cases/${id}/charts/${chartId}`, { method: "DELETE" }),
  assist: (id: number, task: string, extra: string) =>
    api<{ assist_id: number; job_id: string }>(`/patents/cases/${id}/assists`, { method: "POST", json: { task, extra } }),
  review: (id: number, assistId: number, review_status: string) =>
    api<CaseDetail>(`/patents/cases/${id}/assists/${assistId}/review`, { method: "POST", json: { review_status } }),
  removeAssist: (id: number, assistId: number) => api<CaseDetail>(`/patents/cases/${id}/assists/${assistId}`, { method: "DELETE" }),
  addLiterature: (body: { title: string; text: string; url: string; public: boolean }) =>
    api<{ job_id: string }>("/patents/literature", { method: "POST", json: body }),
  analytics: () => api<PatentAnalytics>("/patents/analytics"),
  analyticsMap: () => api<PatentMap>("/patents/analytics/map"),
  refreshBiblio: () => api<{ job_id: string }>("/patents/analytics/refresh-biblio", { method: "POST" }),
  patchBiblio: (ref: string, fields: Partial<Biblio>) =>
    api<Biblio>(`/patents/documents/${encodeURIComponent(ref)}/biblio`, { method: "PATCH", json: { fields } }),
  similar: (id: number) => api<{ query: string; results: SimilarHit[] }>(`/patents/cases/${id}/similar`),
  sources: () => api<{ sources: PatentSource[] }>("/patents/sources"),
  setSourceKeys: (values: Record<string, string>) =>
    api<{ sources: PatentSource[] }>("/patents/sources/keys", { method: "PUT", json: { values } }),
  fetchSource: (source: string, body: { query: string; limit: number; approved_public: boolean }) =>
    api<{ job_id: string }>(`/patents/sources/${encodeURIComponent(source)}/fetch`, { method: "POST", json: body }),
  watches: (id: number) => api<{ watches: Watch[]; hits: WatchHit[] }>(`/patents/cases/${id}/watches`),
  createWatch: (id: number, body: WatchBody) => api<Watch>(`/patents/cases/${id}/watches`, { method: "POST", json: body }),
  updateWatch: (id: number, watchId: number, body: WatchBody) =>
    api<Watch>(`/patents/cases/${id}/watches/${watchId}`, { method: "PUT", json: body }),
  removeWatch: (id: number, watchId: number) => api(`/patents/cases/${id}/watches/${watchId}`, { method: "DELETE" }),
  runWatch: (id: number, watchId: number) => api<{ job_id: string }>(`/patents/cases/${id}/watches/${watchId}/run`, { method: "POST" }),
  decideHit: (id: number, hitId: number, body: { status: string; category?: string }) =>
    api<{ id: number; status: string }>(`/patents/cases/${id}/watch-hits/${hitId}`, { method: "POST", json: body }),
};
