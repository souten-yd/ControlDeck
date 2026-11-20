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
  assist: (id: number, task: string, extra: string) =>
    api<{ assist_id: number; job_id: string }>(`/patents/cases/${id}/assists`, { method: "POST", json: { task, extra } }),
  review: (id: number, assistId: number, review_status: string) =>
    api<CaseDetail>(`/patents/cases/${id}/assists/${assistId}/review`, { method: "POST", json: { review_status } }),
  removeAssist: (id: number, assistId: number) => api<CaseDetail>(`/patents/cases/${id}/assists/${assistId}`, { method: "DELETE" }),
  addLiterature: (body: { title: string; text: string; url: string; public: boolean }) =>
    api<{ job_id: string }>("/patents/literature", { method: "POST", json: body }),
};
