/** TTS / ASR のエンジン（Models の「TTS / ASR」タブ）。 */
import { ApiError, api } from "./client";

export type SpeechKind = "asr" | "tts";
export interface SpeechArchive {
  path: string; complete: boolean; git: Record<string, boolean>;
  model_files: number; model_files_total: number; missing: string[]; wheels: string[]; bytes: number; error: string;
}
export interface SpeechEngine {
  id: string; name: string; kind: SpeechKind; summary: string;
  ready: boolean; installed: boolean; installing: boolean; job_id: string | null;
  runtime: string; model: string; model_bytes: number; storage: string; error: string;
  voices?: string[]; languages?: string[]; license?: string; archive?: SpeechArchive;
}
export interface SpeechEngines {
  selected: Record<SpeechKind, string>;
  engines: SpeechEngine[];
  active_asr: string | null;
}

export const getSpeechEngines = () => api<SpeechEngines>("/speech/engines");
export const selectSpeechEngine = (kind: SpeechKind, engine: string) =>
  api<SpeechEngines>("/speech/engines", { method: "PUT", json: { [kind]: engine } });
export const installSpeechEngine = (id: string) =>
  api<{ job_id: string; reused: boolean }>(`/speech/engines/${encodeURIComponent(id)}/install-jobs`, { method: "POST", json: {} });

/** 選んだ TTS で WAV を作る。音声は JSON ではないので api() を通さずに取る（エラーは同じ形で返す）。 */
export async function synthesizeSpeech(body: { text: string; voice: string; lang: string; speed: number }): Promise<Blob> {
  const res = await fetch("/api/v1/speech/tts", {
    method: "POST", credentials: "same-origin",
    headers: { "X-Requested-With": "ControlDeck", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `エラー (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch { /* JSON でない */ }
    throw new ApiError(res.status, detail);
  }
  return res.blob();
}
