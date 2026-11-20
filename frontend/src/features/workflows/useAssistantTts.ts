/** AI アシスタントの読み上げ（TTS 返答・会話モード）。
 *
 * 回答の生成中から読み始め（feed）、合成した音声を順番どおりに、つなぎ目なしで並べて鳴らす（Web Audio の予約再生）。
 * 2026-11-20 利用者「長文を連続で読ませると飛び飛びになる。ストリーミング再生のように順番を守って再生して」。
 * 原因: Supertonic 3 の音声は前後に約 0.4 秒ずつ無音が付く（実測 15〜138 字で 0.31〜0.44 秒）。それを 1 文ずつ
 * audio 要素で鳴らしていたので、文の切れ目ごとに約 1 秒途切れていた。合成自体は速い（15 字 0.6 秒・138 字 1.9 秒）。
 * - 各音声の前後の無音を削り、文の間は SENTENCE_GAP_S だけ空ける
 * - 前の音声の終わりの時刻に次を予約する（鳴らし終わるのを待ってから次を始めない）
 * - 生成中は、ある程度たまってからまとめて合成する（1 文ずつだと切れ目と抑揚の途切れが増える）。
 *   ただし予約した音声の残りが少なくなったら、たまった分だけで合成する（声を止めない）
 * 声と速さは Models の TTS で選んだ既定（サーバー側）。iPhone は利用者の操作の中で AudioContext を動かさないと
 * 後から鳴らせないので、モードを入れたときなどに unlock() を呼ぶ。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { synthesizeSpeech } from "../../api/speech";
import { preparePlayback } from "../../lib/audioSession";
import { nextSpoken, speechChunks, speechLang, voicedRange } from "./speakable";

/** 文の間に空ける時間（無音を削った後） */
const SENTENCE_GAP_S = 0.18;
/** 生成中にまとめて合成する目安の文字数（最初の 1 回は早く鳴らすため短く） */
const FIRST_CHARS = 60;
const BATCH_CHARS = 160;
/** 予約した音声の残りがこれより少なければ、たまった分だけで合成する */
const LOW_WATER_S = 3;
interface Stream {
  id: string;
  queued: string;
  /** この回答の音はすべてここを通す。止めたら外して、予約済みの音もまとめて消す */
  gain: GainNode | null;
  pending: string;
  done: boolean;
  wake: (() => void) | null;
  finished: Promise<boolean>;
}

export function useAssistantTts({ onError }: { onError: (message: string) => void }) {
  const ctxRef = useRef<AudioContext | null>(null);
  const runRef = useRef(0);
  const streamRef = useRef<Stream | null>(null);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  // 止めた回答。生成の続きが届いても読み直さない（2026-11-20 本番「止めたのに鳴り続けた」: 止めた直後に届いた続きで、
  // 同じ回答を最初から読み始めていた）
  const stoppedIdRef = useRef<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const context = () => {
    if (!ctxRef.current) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      ctxRef.current = new Ctor();
    }
    return ctxRef.current;
  };

  /** 利用者の操作の中で呼ぶ（iPhone で後から鳴らせるようにする） */
  const unlock = useCallback(() => {
    try {
      const ctx = context();
      if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
      // 無音を一度鳴らす（iOS は操作の中で音を出した AudioContext だけ後から鳴らせる）
      const source = ctx.createBufferSource();
      source.buffer = ctx.createBuffer(1, 1, 22050);
      source.connect(ctx.destination);
      source.start();
    } catch {
      // 音を出せない環境: 読み上げの時に知らせる
    }
  }, []);

  const stop = useCallback(() => {
    runRef.current += 1;
    const stream = streamRef.current;
    streamRef.current = null;
    if (stream) {
      stream.done = true;
      stream.wake?.();
      stoppedIdRef.current = stream.id;
      try { stream.gain?.disconnect(); } catch { /* 外れている */ }
    }
    for (const source of sourcesRef.current) {
      try { source.stop(); } catch { /* まだ始まっていない・終わった */ }
    }
    sourcesRef.current = [];
    setSpeaking(false);
  }, []);

  const playStream = async (stream: Stream, run: number): Promise<boolean> => {
    // iPhone の Web Audio はマナーモードで消える。マイクを使っていなければ再生用にする（lib/audioSession）
    preparePlayback();
    const ctx = context();
    if (ctx.state !== "running") await ctx.resume().catch(() => undefined);
    if (run !== runRef.current) return false;
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    stream.gain = gain;
    if (ctx.state !== "running") {
      onErrorRef.current("ブラウザが自動再生を止めました。読み上げをいったん切って入れ直してください");
      return false;
    }
    let endAt = 0;          // 予約した音声が鳴り終わる時刻（ctx.currentTime の目盛り）
    let first = true;
    let firstWaited = false;
    let spoke = false;
    const remaining = () => Math.max(0, endAt - ctx.currentTime);
    const waitFor = (ms: number) => new Promise<void>((resolve) => {
      const timer = window.setTimeout(() => { stream.wake = null; resolve(); }, ms);
      stream.wake = () => { window.clearTimeout(timer); stream.wake = null; resolve(); };
    });
    try {
      while (run === runRef.current) {
        // 合成に回す分を決める: 生成が終わった・たまった・予約の残りが少ない、のどれか
        const want = first ? FIRST_CHARS : BATCH_CHARS;
        const ready = stream.pending.length > 0 && (stream.done || stream.pending.length >= want
          || (first ? firstWaited : remaining() < LOW_WATER_S));
        if (!ready) {
          if (stream.done && !stream.pending) break;
          if (first && stream.pending) {
            // 最初の文が短い（挨拶など）: 少しだけ続きを待ち、来なければその分で鳴らし始める
            await waitFor(400);
            firstWaited = true;
            continue;
          }
          // 予約した音声が残っているうちは待って、まとめて合成する（続きが流れてくると起きる）
          await waitFor(stream.pending ? Math.max(100, (remaining() - LOW_WATER_S) * 1000) : 2000);
          continue;
        }
        const text = stream.pending;
        stream.pending = "";
        first = false;
        for (const chunk of speechChunks(text, 300, 300)) {
          const blob = await synthesizeSpeech({ text: chunk, lang: speechLang(chunk) });
          if (run !== runRef.current) return false;
          const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
          if (run !== runRef.current) return false;
          const [from, to] = voicedRange(decoded.getChannelData(0), decoded.sampleRate);
          if (to <= from) continue;
          const at = Math.max(ctx.currentTime + 0.05, endAt + (spoke ? SENTENCE_GAP_S : 0));
          const source = ctx.createBufferSource();
          source.buffer = decoded;
          source.connect(gain);
          source.onended = () => { sourcesRef.current = sourcesRef.current.filter((s) => s !== source); };
          source.start(at, from / decoded.sampleRate, (to - from) / decoded.sampleRate);
          sourcesRef.current.push(source);
          endAt = at + (to - from) / decoded.sampleRate;
          spoke = true;
          setSpeaking(true);
        }
      }
      // 予約した音声が鳴り終わるまで待つ（会話モードは読み終えてから聞き直す）
      while (run === runRef.current && remaining() > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(250, remaining() * 1000 + 20)));
      }
      return spoke && run === runRef.current;
    } catch (error) {
      if (run === runRef.current) onErrorRef.current(error instanceof Error ? error.message : "読み上げに失敗しました");
      return false;
    } finally {
      if (run === runRef.current) setSpeaking(false);
    }
  };

  /** 生成中の回答を、流れてきた分から順に読む（同じ id で何度でも呼ぶ。done で残りを読み切る） */
  const feed = useCallback((id: string, markdown: string, done: boolean) => {
    let stream = streamRef.current;
    if (!stream || stream.id !== id) {
      if (stoppedIdRef.current === id) return;   // 止めた回答は読み直さない
      stop();
      stoppedIdRef.current = null;
      const run = runRef.current;
      const created: Stream = { id, queued: "", gain: null, pending: "", done: false, wake: null, finished: Promise.resolve(false) };
      streamRef.current = created;
      created.finished = playStream(created, run);
      stream = created;
    }
    if (stream.done) return;
    const next = nextSpoken(markdown, stream.queued, done);
    stream.queued = next.queued;
    stream.pending += next.text;
    if (done || next.capped) stream.done = true;
    stream.wake?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stop]);

  /** feed で読んでいる回答を読み終える（止める・失敗する）まで待つ。読んでいなければすぐ false */
  const finished = useCallback((id: string): Promise<boolean> =>
    streamRef.current?.id === id ? streamRef.current.finished : Promise.resolve(false), []);

  /** 回答の Markdown をまとめて読む。読み終わる（または止める・失敗する）と解決する。読めたら true */
  const speak = useCallback(async (markdown: string): Promise<boolean> => {
    const id = `speak-${Date.now()}-${Math.random()}`;
    feed(id, markdown, true);
    return finished(id);
  }, [feed, finished]);

  useEffect(() => () => {
    runRef.current += 1;
    for (const source of sourcesRef.current) { try { source.stop(); } catch { /* 終わった */ } }
    void ctxRef.current?.close().catch(() => undefined);
  }, []);

  return { speak, feed, finished, stop, unlock, speaking };
}
