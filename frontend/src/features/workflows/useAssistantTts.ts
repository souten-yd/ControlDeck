/** AI アシスタントの読み上げ（TTS 返答・会話モード）。
 *
 * 文の区切りごとに合成し、鳴らしている間に次を作る（最初の音が早く出る。Supertonic 3 は 6.5 秒を 0.8 秒で作る）。
 * 声と速さは Models の TTS で選んだ既定（サーバー側）。iPhone は利用者の操作の中で一度鳴らした
 * audio 要素でないと後から鳴らせないので、モードを入れたときに unlock() で無音を鳴らしておく。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "../../api/speech";
import { speakableText, speechChunks, speechLang } from "./speakable";

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
    const url = silentWav();
    audio.src = url;
    void audio.play().catch(() => undefined).finally(() => URL.revokeObjectURL(url));
  }, []);

  const stop = useCallback(() => {
    runRef.current += 1;
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

  useEffect(() => () => { runRef.current += 1; audioRef.current?.pause(); releaseUrl(); }, []);

  return { speak, stop, unlock, speaking };
}
