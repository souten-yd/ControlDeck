/** AI アシスタントの音声入力。
 *
 * 2026-11-18 利用者「Claude のように波形が出て、音声が入っているのを確かめながら文字入力したい」「すぐ止まった。
 * 長時間の入力を想定して」。前は 1.2 秒黙ると止まって送信し、最長 30 秒だった。
 *
 * - 止めるボタン（✓）を押すまで聞き続ける（最長 60 分）。黙っても止まらない
 * - 話の切れ目ごとに区切って文字にし、入力欄へ足していく（送信はしない。直してから送れる）。
 *   区切りは「話した後の 0.7 秒の無音」。長く話し続けたら 12 秒からの短い無音、最長 15 秒で区切る
 *   （whisper は 20 秒を超えると繰り返しで崩れることがあった。8 秒前後なら約 3 秒で文字になる。実測）
 * - 波形は直近の音の大きさの棒で出す。✕ で取り消すと、この回に足した文字を戻す */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "../../api/client";

export type AsrPhase = "idle" | "installing" | "permission" | "listening" | "finishing" | "error";
interface AsrStatus { ready: boolean; installing: boolean; job_id: string | null; upgrade_available?: boolean }
interface JobStatus { status: string; error?: string }

export const WAVE_BARS = 48;
const TICK_MS = 50;
const VOICE_RMS = 0.02;
const PAUSE_MS = 700;
const SOFT_CUT_MS = 12_000;
const SOFT_PAUSE_MS = 150;
const HARD_CUT_MS = 15_000;
// 黙っている間は 3 秒ごとに録り直す（区切りの頭に長い無音が付くと、whisper が無い言葉を書くことがある。
// 長さは話し始めから数える。レビュー指摘 2026-11-18）
const SILENT_RESTART_MS = 3_000;
export const MAX_RECORDING_MS = 60 * 60_000;

/** 波形と経過時間は、ここを購読する部品だけが描き直す（会話全体を 1 秒に 20 回描き直さない。レビュー指摘） */
export interface VoiceMeter { levels: number[]; elapsedMs: number }
function createMeter() {
  let value: VoiceMeter = { levels: Array(WAVE_BARS).fill(0), elapsedMs: 0 };
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set: (next: VoiceMeter) => { value = next; listeners.forEach((l) => l()); },
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  };
}
export type VoiceMeterStore = ReturnType<typeof createMeter>;
export function useVoiceMeter(store: VoiceMeterStore): VoiceMeter {
  return useSyncExternalStore(store.subscribe, store.get);
}

export function useAssistantAsr({ onText, onError }: {
  /** 区切りの文字が届いたとき（古い順） */
  onText: (text: string) => void;
  onError: (message: string) => void;
}) {
  const [phase, setPhase] = useState<AsrPhase>("idle");
  const meterRef = useRef<VoiceMeterStore | null>(null);
  if (!meterRef.current) meterRef.current = createMeter();
  const meter = meterRef.current;
  const resetMeter = () => meter.set({ levels: Array(WAVE_BARS).fill(0), elapsedMs: 0 });
  const [pending, setPending] = useState(0);
  const pendingRef = useRef(0);
  // 会話モード: 読み上げ・回答待ちの間は聞かない（自分の声を拾わない）。最後に声がした時刻で話し終わりを見る
  const pausedRef = useRef(false);
  const lastVoiceRef = useRef(0);
  const cutRef = useRef<((keep: boolean) => void) | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef(0);
  const wakeRef = useRef<{ release: () => Promise<void> } | null>(null);
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  // 取り消し・やり直しで古い区切りの結果を捨てるための番号
  const sessionRef = useRef(0);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const release = () => {
    cutRef.current = null;
    pausedRef.current = false;
    lastVoiceRef.current = 0;
    window.clearInterval(timerRef.current);
    timerRef.current = 0;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close().catch(() => undefined);
    contextRef.current = null;
    void wakeRef.current?.release().catch(() => undefined);
    wakeRef.current = null;
  };

  /** 区切りを 1 つ文字にする（順番を守るため、前の区切りが終わってから） */
  const transcribe = (blob: Blob, session: number) => {
    setPending((n) => n + 1);
    pendingRef.current += 1;
    chainRef.current = chainRef.current.then(async () => {
      try {
        if (session !== sessionRef.current) return;
        const body = new FormData();
        body.append("audio", blob, blob.type.includes("mp4") ? "voice.m4a" : "voice.webm");
        const result = await api<{ text: string }>("/chat/asr/transcribe", { method: "POST", body });
        const text = result.text.trim();
        if (text && session === sessionRef.current) onTextRef.current(text);
      } catch (error) {
        // 無音・雑音だけの区切りは黙って捨てる。それ以外は知らせて続ける
        const message = error instanceof Error ? error.message : "";
        if (session === sessionRef.current && message && !message.includes("認識できません")) onErrorRef.current(message);
      } finally {
        setPending((n) => Math.max(0, n - 1));
        pendingRef.current = Math.max(0, pendingRef.current - 1);
      }
    });
  };

  const newRecorder = (stream: MediaStream, session: number) => {
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    const state = { voiced: false, keep: true };
    recorder.ondataavailable = (event) => { if (event.data.size > 0) chunks.push(event.data); };
    recorder.onstop = () => {
      if (!state.keep || !state.voiced || chunks.length === 0) return;
      transcribe(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }), session);
    };
    recorder.start(250);
    return { recorder, state };
  };

  const ensureInstalled = async () => {
    const current = await api<AsrStatus>("/chat/asr/status");
    if (current.ready) {
      // いまのエンジンで使いながら、速いモデルを裏で入れる（待たせない）
      if (current.upgrade_available && !current.job_id) void api("/chat/asr/install-jobs", { method: "POST" }).catch(() => undefined);
      return;
    }
    setPhase("installing");
    const installation = current.job_id
      ? { job_id: current.job_id }
      : await api<{ job_id: string }>("/chat/asr/install-jobs", { method: "POST" });
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const job = await api<JobStatus>(`/jobs/${installation.job_id}`);
      if (job.status === "succeeded") return;
      if (["failed", "canceled", "interrupted"].includes(job.status)) {
        throw new Error(job.error || "音声入力モデルの導入に失敗しました");
      }
    }
  };

  const start = async () => {
    if (phase !== "idle" && phase !== "error") return;
    const session = ++sessionRef.current;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("この接続ではマイクを利用できません。HTTPSまたはlocalhostで開いてください");
      }
      await ensureInstalled();
      if (session !== sessionRef.current) return;   // 取り消された
      setPhase("permission");
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (session !== sessionRef.current) { stream.getTracks().forEach((t) => t.stop()); return; }
      streamRef.current = stream;
      try {
        // 長く話している間に画面が消えると、マイクも止まる（携帯）
        const wake = (navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } }).wakeLock;
        wakeRef.current = wake ? await wake.request("screen") : null;
      } catch { wakeRef.current = null; }
      if (session !== sessionRef.current) {          // 待っている間に取り消された（レビュー指摘）
        release();
        return;
      }
      const context = new AudioContext();
      contextRef.current = context;
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      context.createMediaStreamSource(stream).connect(analyser);
      const samples = new Uint8Array(analyser.fftSize);
      let current = newRecorder(stream, session);
      recorderRef.current = current.recorder;
      const startedAt = performance.now();
      let segmentAt = startedAt;
      let spokeAt = 0;      // この区切りで話し始めた時刻（0 は、まだ話していない）
      let lastVoiceAt = 0;
      const bars = Array(WAVE_BARS).fill(0) as number[];
      resetMeter();
      setPhase("listening");
      const cut = () => {
        const previous = current;
        if (pausedRef.current) previous.state.keep = false;
        current = newRecorder(stream, session);   // 先に次を録り始める（切れ目を落とさない）
        recorderRef.current = current.recorder;
        previous.recorder.stop();
        segmentAt = performance.now();
        spokeAt = 0;
      };
      cutRef.current = (keep: boolean) => {
        if (!keep) current.state.keep = false;
        cut();
      };
      timerRef.current = window.setInterval(() => {
        analyser.getByteTimeDomainData(samples);
        let energy = 0;
        for (const sample of samples) {
          const normalized = (sample - 128) / 128;
          energy += normalized * normalized;
        }
        const rms = Math.sqrt(energy / samples.length);
        const now = performance.now();
        bars.shift();
        bars.push(Math.min(1, rms * 9));
        meter.set({ levels: [...bars], elapsedMs: now - startedAt });
        if (rms >= VOICE_RMS && !pausedRef.current) {
          if (!current.state.voiced) spokeAt = now;
          current.state.voiced = true;
          lastVoiceAt = now;
          lastVoiceRef.current = now;
        }
        const length = current.state.voiced ? now - spokeAt : now - segmentAt;
        const quiet = now - lastVoiceAt;
        if (current.state.voiced) {
          if ((quiet >= PAUSE_MS && length >= 1500) || (length >= SOFT_CUT_MS && quiet >= SOFT_PAUSE_MS) || length >= HARD_CUT_MS) cut();
        } else if (length >= SILENT_RESTART_MS) {
          current.state.keep = false;   // 黙っている間の録音は捨てて録り直す（大きくしない）
          cut();
        }
        if (now - startedAt >= MAX_RECORDING_MS) {
          onErrorRef.current("60 分になったので止めました");
          void finish();
        }
      }, TICK_MS);
    } catch (error) {
      release();
      setPhase("error");
      onErrorRef.current(error instanceof Error ? error.message : "音声入力に失敗しました");
    }
  };

  /** 止めて、残りの区切りを文字にし終えるのを待つ（入力欄の文字は残す） */
  const finish = async () => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    window.clearInterval(timerRef.current);
    if (recorder && recorder.state !== "inactive") {
      await new Promise<void>((resolve) => {
        recorder.addEventListener("stop", () => resolve(), { once: true });
        recorder.stop();
      });
    }
    release();
    setPhase("finishing");
    const session = sessionRef.current;
    await chainRef.current;
    if (session === sessionRef.current) setPhase("idle");
    resetMeter();
  };

  /** 取り消す（録音も、文字にしている途中の区切りも捨てる） */
  const cancel = () => {
    sessionRef.current += 1;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    release();
    setPhase("idle");
    resetMeter();
  };

  useEffect(() => () => {
    sessionRef.current += 1;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    release();
  }, []);

  /** 会話モード: 聞くのを止める（録っている途中の音は捨てる）／再開する */
  const pause = () => {
    pausedRef.current = true;
    cutRef.current?.(false);
  };
  const resume = () => {
    cutRef.current?.(false);   // 止めている間に録れた音（読み上げの声）を捨ててから聞き直す
    lastVoiceRef.current = 0;
    pausedRef.current = false;
  };
  /** 最後に声がしてからの時間（ミリ秒）。まだ話していなければ Infinity */
  const quietMs = () => (lastVoiceRef.current ? performance.now() - lastVoiceRef.current : Infinity);
  /** 文字にしている途中の区切りが無い */
  const settled = () => pendingRef.current === 0;

  return { phase, meter, pending, start, finish, cancel, pause, resume, quietMs, settled,
    listening: phase === "listening", active: phase === "listening" || phase === "finishing" || phase === "permission" };
}
