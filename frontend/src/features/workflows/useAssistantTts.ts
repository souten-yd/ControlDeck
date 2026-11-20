/** AI アシスタントの読み上げ（TTS 返答・会話モード）。
 *
 * 文の区切りごとに合成し、鳴らしている間に次を作る（最初の音が早く出る。Supertonic 3 は 6.5 秒を 0.8 秒で作る）。
 * 回答の生成中から読み始める（feed）: 文の終わりまで流れてきた分を順に合成して鳴らす。2026-11-20 利用者「長文だと
 * 生成し終えるまで何も再生されずテンポが悪い。デコードの速さを考えて途切れないよう順次再生して」。
 * 生成（毎秒数十文字）は読み上げ（毎秒 8 文字前後）よりずっと速いので、最初の文を鳴らしている間に次がそろう。
 * 声と速さは Models の TTS で選んだ既定（サーバー側）。iPhone は利用者の操作の中で一度鳴らした
 * audio 要素でないと後から鳴らせないので、モードを入れたときに unlock() で無音を鳴らしておく。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "../../api/speech";
import { nextSpoken, speakableText, speechChunks, speechLang } from "./speakable";

interface Stream { id: string; queued: number; pending: string[]; done: boolean; wake: (() => void) | null; finished: Promise<boolean> }

// 0.1 秒の無音（44.1 kHz・16 bit・1 ch の WAV）
function silentWav(): string {
  const samples = 4410;
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
  write(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); write(8, "WAVE"); write(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 44100, true); view.setUint32(28, 88200, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, "data"); view.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

export function useAssistantTts({ onError }: { onError: (message: string) => void }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const runRef = useRef(0);
  const urlRef = useRef<string | null>(null);
  const stopWaitRef = useRef<(() => void) | null>(null);
  const streamRef = useRef<Stream | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const element = () => {
    if (!audioRef.current) audioRef.current = new Audio();
    return audioRef.current;
  };
  const releaseUrl = () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  };

  /** 利用者の操作の中で呼ぶ（iPhone で後から鳴らせるようにする） */
  const unlock = useCallback(() => {
    const audio = element();
    if (!audio.paused) return;   // 読み上げ中は邪魔しない（もう鳴らせる状態）
    const url = silentWav();
    audio.src = url;
    void audio.play().catch(() => undefined).finally(() => URL.revokeObjectURL(url));
  }, []);

  const stop = useCallback(() => {
    runRef.current += 1;
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) { stream.done = true; stream.wake?.(); }
    const audio = audioRef.current;
    if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
    stopWaitRef.current?.();
    stopWaitRef.current = null;
    releaseUrl();
    setSpeaking(false);
  }, []);

  const play = (blob: Blob, run: number) => new Promise<void>((resolve) => {
    if (run !== runRef.current) { resolve(); return; }
    const audio = element();
    releaseUrl();
    urlRef.current = URL.createObjectURL(blob);
    const done = () => {
      audio.removeEventListener("ended", done);
      audio.removeEventListener("error", done);
      stopWaitRef.current = null;
      resolve();
    };
    stopWaitRef.current = done;
    audio.addEventListener("ended", done);
    audio.addEventListener("error", done);
    audio.src = urlRef.current;
    audio.play().catch((error: unknown) => {
      onErrorRef.current(error instanceof Error && error.name === "NotAllowedError"
        ? "ブラウザが自動再生を止めました。読み上げをいったん切って入れ直してください" : "音声を再生できませんでした");
      done();
    });
  });

  /** 回答の Markdown を読む。読み終わる（または止める・失敗する）と解決する。読めたら true */
  const speak = useCallback(async (markdown: string): Promise<boolean> => {
    stop();
    const run = runRef.current;
    const text = speakableText(markdown);
    const chunks = speechChunks(text);
    if (chunks.length === 0) return false;
    const lang = speechLang(text);
    setSpeaking(true);
    try {
      let next: Promise<Blob> = synthesizeSpeech({ text: chunks[0], lang });
      for (let i = 0; i < chunks.length; i++) {
        const blob = await next;
        if (run !== runRef.current) return false;
        if (i + 1 < chunks.length) {
          next = synthesizeSpeech({ text: chunks[i + 1], lang });
          next.catch(() => undefined);   // 止めたときに未処理の失敗を出さない
        }
        await play(blob, run);
        if (run !== runRef.current) return false;
      }
      return true;
    } catch (error) {
      if (run === runRef.current) onErrorRef.current(error instanceof Error ? error.message : "読み上げに失敗しました");
      return false;
    } finally {
      if (run === runRef.current) { releaseUrl(); setSpeaking(false); }
    }
  }, [stop]);

  /** 生成中の回答を、流れてきた分から順に読む（同じ id で何度でも呼ぶ。done で残りを読み切る） */
  const feed = useCallback((id: string, markdown: string, done: boolean) => {
    let stream = streamRef.current;
    if (!stream || stream.id !== id) {
      stop();
      const run = runRef.current;
      const created: Stream = { id, queued: 0, pending: [], done: false, wake: null, finished: Promise.resolve(false) };
      created.finished = playStream(created, run);
      streamRef.current = created;
      stream = created;
    }
    if (stream.done) return;
    const next = nextSpoken(markdown, stream.queued, done);
    stream.queued = next.queued;
    if (next.text) stream.pending.push(next.text);
    if (done || next.capped) stream.done = true;
    stream.wake?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stop]);

  /** feed で読んでいる回答を読み終える（止める・失敗する）まで待つ。読んでいなければすぐ false */
  const finished = useCallback((id: string): Promise<boolean> =>
    streamRef.current?.id === id ? streamRef.current.finished : Promise.resolve(false), []);

  const playStream = async (stream: Stream, run: number): Promise<boolean> => {
    const chunks: string[] = [];
    let first = true;
    let spoke = false;
    const pull = () => {
      if (!stream.pending.length) return;
      const text = stream.pending.join("");
      stream.pending = [];
      // 最初は短く（早く鳴らし始める）、後はたまった分を長めにまとめる（つなぎ目を減らす）
      chunks.push(...speechChunks(text, first ? 80 : 300, 300));
      first = false;
    };
    const synth = (text: string) => {
      const job = synthesizeSpeech({ text, lang: speechLang(text) });
      job.catch(() => undefined);   // 止めたときに未処理の失敗を出さない
      return job;
    };
    let current: Promise<Blob> | null = null;
    try {
      while (run === runRef.current) {
        pull();
        if (!current) {
          if (!chunks.length) {
            if (stream.done) break;
            await new Promise<void>((resolve) => { stream.wake = resolve; });
            stream.wake = null;
            continue;
          }
          setSpeaking(true);
          current = synth(chunks.shift()!);
        }
        const blob = await current;
        current = null;
        if (run !== runRef.current) return false;
        pull();
        if (chunks.length) current = synth(chunks.shift()!);   // 鳴らしている間に次を作る
        await play(blob, run);
        spoke = true;
      }
      return spoke && run === runRef.current;
    } catch (error) {
      if (run === runRef.current) onErrorRef.current(error instanceof Error ? error.message : "読み上げに失敗しました");
      return false;
    } finally {
      if (run === runRef.current) { releaseUrl(); setSpeaking(false); }
    }
  };

  useEffect(() => () => { runRef.current += 1; audioRef.current?.pause(); releaseUrl(); }, []);

  return { speak, feed, finished, stop, unlock, speaking };
}
