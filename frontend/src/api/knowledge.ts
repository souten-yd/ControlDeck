/** ナレッジ（横断検索・文書・規格・PDF の取り込み）API の型。backend/app/knowledge/router.py と対になる。 */
import { api } from "./client";

export type KnowledgeScope = "all" | "general" | "patents" | "standards";
export interface FoundDocument {
  ref: string; title: string; collection: string; relation: string; via: string; sensitivity: string;
  passages: { text: string; locator: Record<string, unknown> }[];
}
export interface DocumentAsset { sha256: string; role: string; ord: number; mime: string; bytes: number; url: string; meta: Record<string, unknown> }
export interface DocumentDetail {
  ref: string; title: string; collection: string; collection_kind: string; source: string; url: string;
  sensitivity: string; basis: string; ai_use: string; text: string; text_truncated: boolean; chunk_count: number;
  meta: Record<string, unknown>;
  identifiers: { scheme: string; value: string; edition: string; raw: string }[];
  references: { scheme: string; value: string; edition: string; raw: string }[];
  assets: DocumentAsset[];
  annotations: { id: number; kind: string; basis: string; review_status: string; content: string; stale?: boolean }[];
  analysis_kinds?: Record<string, string>;
  related: { ref: string; relation: string; via: string; title: string; collection: string }[];
}
/** 知識エッセンス（K1）: ローカル専用の AI が文献から抜き出した知識。引用は原文の [start, end) と一致するものだけ。 */
export interface EssenceReview {
  decision: string; note: string; reviewer: string; at: number; stale: boolean;
}
export interface EssenceItem {
  kind: string; summary: string; quote: string; start: number; end: number;
  basis: string; review_status: string; review: EssenceReview | null;
}
export interface EssenceRow {
  id: number; doc_id: number; source_revision: string; items: EssenceItem[]; dropped: number;
  status: string; error: string; job_id: string; created: number; updated: number;
  basis: string; stale: boolean; shard: string;
}
/** 知識の横断（K2）: 承認済みのエッセンスを文献ごとに横断して探した結果の 1 件。 */
export interface CrossItem {
  essence_ref: string; item_index: number;
  doc_ref: string; doc_title: string;
  kind: string; summary: string; quote: string; reviewed_at: number | null;
}
/** 比較の表のセルの中身（文献は列の見出しで分かるので、セルに文献の情報は入らない）。 */
export type CrossCompareCell = Omit<CrossItem, "doc_ref" | "doc_title">;
export interface CrossSearchResult {
  items: CrossItem[]; next_cursor: number | null; limit: number; kinds: Record<string, string>;
}
export interface CrossCompareResult {
  documents: { ref: string; title: string }[];
  rows: { kind: string; label: string; cells: CrossCompareCell[][] }[];
  kinds: Record<string, string>;
}
/** 種類の 8 種（backend/app/knowledge/essence.py の KINDS と同じ。初回検索前に絞り込みを選べるように固定で持つ）。 */
export const ESSENCE_KINDS: Record<string, string> = {
  problem: "課題", cause: "原因", principle: "原理", mechanism: "作用機構",
  implementation: "実装", effect: "効果", constraint: "制約", application: "応用",
};
export interface LicenseDecision {
  storage: string; ai_use: string; operations: string[]; needs_review: boolean; reasons: string[]; publisher: string | null;
}
export interface StandardRow {
  ref: string; designation: string; title: string; edition: string; status: string; storage: string; ai_use: string;
  needs_review: boolean; reasons: string[]; chunks: number;
}
export interface StandardHit {
  ref: string; designation: string; title: string; edition: string; status: string; exact: boolean; fulltext: boolean;
  clauses: { clause: string; heading: string; page: number | null; snippet: string }[];
}
export interface StandardInput {
  designation: string; title: string; edition: string; instrument: string; text: string; pdf_path: string;
  permission_ref: string; reviewer: string; reviewed_on: string;
  granted_storage: string | null; granted_ai_use: string | null;
}

export interface RebuildPlan { retag_ai_answers: number; reembed: number; reingest_pdf: number; describe_figures?: number }

export const knowledgeApi = {
  rebuildPlan: () => api<RebuildPlan>("/knowledge/rebuild"),
  rebuild: (describe_figures: boolean) => api<{ job_id: string }>("/knowledge/rebuild", { method: "POST", json: { describe_figures } }),
  find: (query: string, scope: KnowledgeScope) =>
    api<{ results: FoundDocument[] }>("/knowledge/find", { method: "POST", json: { query, scope, limit: 12 } }),
  document: (ref: string) => api<DocumentDetail>(`/knowledge/documents/${encodeURIComponent(ref)}`),
  addPdf: (collection: string, path: string, title: string) =>
    api<{ job_id: string }>(`/knowledge/collections/${encodeURIComponent(collection)}/pdf-jobs`, { method: "POST", json: { path, title } }),
  analyze: (ref: string, kind: string) =>
    api<{ job_id: string }>(`/knowledge/documents/${encodeURIComponent(ref)}/analyses`, { method: "POST", json: { kind } }),
  reviewAnnotation: (ref: string, id: number, review_status: string) =>
    api(`/knowledge/documents/${encodeURIComponent(ref)}/annotations/${id}/review`, { method: "POST", json: { review_status } }),
  removeAnnotation: (ref: string, id: number) =>
    api(`/knowledge/documents/${encodeURIComponent(ref)}/annotations/${id}`, { method: "DELETE" }),
  essences: (ref: string) =>
    api<{ essences: EssenceRow[]; kinds: Record<string, string> }>(`/knowledge/documents/${encodeURIComponent(ref)}/essences`),
  extractEssences: (ref: string) =>
    api<{ job_id: string; essence_id: number; essence_ref: string }>(`/knowledge/documents/${encodeURIComponent(ref)}/essences`, { method: "POST" }),
  reviewEssence: (essenceRef: string, index: number, decision: string, note = "") =>
    api<{ essence_id: number; item_index: number; decision: string; reviewer: string }>(
      `/knowledge/essences/${encodeURIComponent(essenceRef)}/items/${index}/review`, { method: "POST", json: { decision, note } }),
  searchEssences: (q: string, kind = "", cursor = 0, limit = 50) =>
    api<CrossSearchResult>("/knowledge/essences/search", { method: "POST", json: { q, kind, cursor, limit } }),
  compareEssences: (refs: string[]) =>
    api<CrossCompareResult>("/knowledge/essences/compare", { method: "POST", json: { refs } }),
  standards: () => api<{ standards: StandardRow[]; instruments: Record<string, string> }>("/knowledge/standards"),
  decide: (body: Partial<StandardInput>) => api<LicenseDecision>("/knowledge/standards/decide", { method: "POST", json: body }),
  addStandard: (body: StandardInput) => api<{ job_id: string }>("/knowledge/standards", { method: "POST", json: body }),
  searchStandards: (query: string, include_superseded = false) =>
    api<{ results: StandardHit[] }>("/knowledge/standards/search", { method: "POST", json: { query, include_superseded } }),
  removeStandard: (ref: string) => api(`/knowledge/standards/${encodeURIComponent(ref)}`, { method: "DELETE" }),
};

/** 利用者向けの言葉（内部の値をそのまま見せない） */
export const RELATION_LABEL: Record<string, string> = {
  exact: "番号が一致", cites: "上の文書が参照", cited_by: "上の文書を参照", version: "別の版", match: "",
};
export const AI_USE_LABEL: Record<string, string> = {
  allowed: "AI に使える", local_only: "ローカルの AI だけ", prohibited: "AI には使わない",
};
export const STORAGE_LABEL: Record<string, string> = {
  shared_server: "本文を保存", personal_copy: "番号と題名だけ（本文は手元の 1 部で読む）", metadata_only: "番号と題名だけ",
};
export const STATUS_LABEL: Record<string, string> = { current: "現行", superseded: "旧版", withdrawn: "廃止", unknown: "状態不明" };
/** エッセンスのレビューの判断（backend/app/knowledge/essence.py の DECISIONS）。 */
export const ESSENCE_DECISION_LABEL: Record<string, string> = { approved: "承認", rejected: "却下", returned: "差し戻し" };
