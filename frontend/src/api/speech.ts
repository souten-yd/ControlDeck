/** TTS / ASR のエンジン（Models の「TTS / ASR」タブ）。 */
import { ApiError, api } from "./client";

export type SpeechKind = "asr" | "tts";
export interface SpeechArchive {
  path: string; complete: boolean; git: Record<string, boolean>;
  model_files: number; model_files_total: number; missing: string[]; wheels: string[]; bytes: number; error: string;
}
export interface SpeechEngine {
  id: string; name: string; kind: SpeechKind; summary: string;
  ready: boolean; installed: boolean; installing: boolean; job_id: string | null; language?: string;
  runtime: string; model: string; model_bytes: number; storage: string; error: string;
  voices?: string[]; languages?: string[]; license?: string; archive?: SpeechArchive;
}
/** 音声入力のフィルター（Models の TTS / ASR → ASR の設定） */
export interface InputFilter {
  enabled: boolean; preset: "recommended" | "noisy" | "quiet" | "custom";
  vad: boolean; vad_threshold: number; min_speech_ms: number; min_level_db: number;
  denoise: "off" | "gtcrn"; highpass: boolean;
  mic_sensitivity: number; turn_end_ms: number; browser_processing: boolean;
}
export interface SpeechSettings {
  asr: string; tts: string;
  tts_voice: string; tts_speed: number; tts_steps: number; tts_language: "auto" | "ja" | "en";
  whisper_language: "ja" | "en" | "auto";
  input_filter: InputFilter;
}
export interface SpeechEngines {
  selected: SpeechSettings;
  engines: SpeechEngine[];
  active_asr: string | null;
  filter: { ready: boolean; gate: { passed: number; no_speech: number; too_quiet: number };
    presets: Record<string, Partial<InputFilter>>; ranges: Record<string, [number, number]> };
}
/** 設定を変える（変える項目だけ。入力フィルターも変える項目だけ送る） */
export const saveSpeechSettings = (values: Partial<Omit<SpeechSettings, "input_filter">> & { input_filter?: Partial<InputFilter> }) =>
  api<SpeechEngines>("/speech/engines", { method: "PUT", json: values });

export const getSpeechEngines = () => api<SpeechEngines>("/speech/engines");
export const selectSpeechEngine = (kind: SpeechKind, engine: string) =>
  api<SpeechEngines>("/speech/engines", { method: "PUT", json: { [kind]: engine } });
/** 読み上げの既定の声・速さ（AI アシスタントの TTS 返答・会話モードが使う） */
export const saveSpeechDefaults = (values: { tts_voice?: string; tts_speed?: number }) =>
  api<SpeechEngines>("/speech/engines", { method: "PUT", json: values });
/** エンジンを外す（Supertonic 3 は実行環境だけ。保管庫は残す） */
export const uninstallSpeechEngine = (id: string) =>
  api<SpeechEngines>(`/speech/engines/${encodeURIComponent(id)}/uninstall`, { method: "POST", json: {} });

export const installSpeechEngine = (id: string) =>
  api<{ job_id: string; reused: boolean }>(`/speech/engines/${encodeURIComponent(id)}/install-jobs`, { method: "POST", json: {} });

/** 選んだ TTS で WAV を作る。音声は JSON ではないので api() を通さずに取る（エラーは同じ形で返す）。 */
/** voice・speed を省くと Models の TTS で選んだ既定の声・速さ */
export async function synthesizeSpeech(body: { text: string; voice?: string; lang: string; speed?: number }): Promise<Blob> {
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
