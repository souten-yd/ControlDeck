import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

type Phase = "idle" | "installing" | "permission" | "listening" | "transcribing" | "muted" | "error";
interface AsrStatus { ready: boolean; installing: boolean; job_id: string | null }
interface JobStatus { status: string; error?: string }

export function useAssistantAsr({ busy, onTranscript, onError }: {
  busy: boolean;
  onTranscript: (text: string) => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [level, setLevel] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const animationRef = useRef(0);
  const chunksRef = useRef<Blob[]>([]);
  const voicedRef = useRef(false);
  const stoppingRef = useRef(false);

  const release = () => {
    cancelAnimationFrame(animationRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
    recorderRef.current = null;
    setLevel(0);
  };

  const fail = (error: unknown) => {
    release();
    setPhase("error");
    onError(error instanceof Error ? error.message : "音声入力に失敗しました");
  };

  const transcribe = async (blob: Blob) => {
    setPhase("transcribing");
    const body = new FormData();
    body.append("audio", blob, "voice.webm");
    const result = await api<{ text: string }>("/chat/asr/transcribe", { method: "POST", body });
    const text = result.text.trim();
    if (!text) throw new Error("音声を認識できませんでした");
    setPhase("muted");
    await onTranscript(text);
  };

  const stop = (recognize = true) => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive" || stoppingRef.current) return;
    stoppingRef.current = true;
    if (!recognize) voicedRef.current = false;
    recorder.stop();
  };

  const beginRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      throw new Error("この接続ではマイクを利用できません。HTTPSまたはlocalhostで開いてください");
    }
    setPhase("permission");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    streamRef.current = stream;
    chunksRef.current = [];
    voicedRef.current = false;
    stoppingRef.current = false;
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorderRef.current = recorder;
    recorder.ondataavailable = (event) => event.data.size > 0 && chunksRef.current.push(event.data);
    recorder.onstop = () => {
      const voiced = voicedRef.current;
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
      release();
      if (!voiced || blob.size === 0) {
        setPhase("idle");
        return;
      }
      void transcribe(blob).catch(fail);
    };
    recorder.start(250);
    setPhase("listening");

    const context = new AudioContext();
    audioContextRef.current = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const startedAt = performance.now();
    let lastVoiceAt = startedAt;
    const monitor = () => {
      analyser.getByteTimeDomainData(samples);
      let energy = 0;
      for (const sample of samples) {
        const normalized = (sample - 128) / 128;
        energy += normalized * normalized;
      }
      const rms = Math.sqrt(energy / samples.length);
      setLevel(Math.min(1, rms * 8));
      const now = performance.now();
      if (rms >= 0.025) {
        voicedRef.current = true;
        lastVoiceAt = now;
      }
      if ((voicedRef.current && now - lastVoiceAt >= 1200) || now - startedAt >= 30_000) {
        stop(true);
        return;
      }
      animationRef.current = requestAnimationFrame(monitor);
    };
    animationRef.current = requestAnimationFrame(monitor);
  };

  const ensureInstalled = async () => {
    const current = await api<AsrStatus>("/chat/asr/status");
    if (current.ready) return;
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

  const toggle = async () => {
    if (phase === "listening") {
      stop(true);
      return;
    }
    if (busyRef.current || !["idle", "error"].includes(phase)) return;
    try {
      await ensureInstalled();
      if (busyRef.current) {
        setPhase("muted");
        return;
      }
      await beginRecording();
    } catch (error) {
      fail(error);
    }
  };

  useEffect(() => {
    if (busy && phase === "listening") stop(false);
    if (!busy && phase === "muted") setPhase("idle");
  }, [busy, phase]);

  useEffect(() => () => {
    stop(false);
    release();
  }, []);

  return { phase, level, toggle, stop: () => stop(true), listening: phase === "listening" };
}
