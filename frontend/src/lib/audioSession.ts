/** iPhone（Safari 16.4 以降）の音声の扱い（navigator.audioSession）をマイクと読み上げで切り替える。
 *
 * 2026-11-20 本番（iPhone）: 読み上げをマナーモードでも鳴らすため "playback" にしたところ、その後にマイクを開くと
 * 「AudioSession category is not compatible with audio capture.」で失敗した。"playback" は再生専用で録音できない。
 * - マイクを開く前は "play-and-record"（録音しながら読み上げも鳴る。会話モードはマイクを開いたまま読む）
 * - 読み上げは、マイクを使っていないときだけ "playback"（マナーモードでも鳴らす）
 * - マイクを全部閉じたら "auto" に戻す
 * audioSession が無いブラウザ（PC・Android・古い Safari）では何もしない。 */

type AudioSessionLike = { type: string };

let captures = 0;

function session(): AudioSessionLike | undefined {
  return typeof navigator === "undefined" ? undefined : (navigator as unknown as { audioSession?: AudioSessionLike }).audioSession;
}

function setType(type: string): void {
  const current = session();
  if (!current || current.type === type) return;
  try { current.type = type; } catch { /* 対応していない種類 */ }
}

/** マイクを開く直前に呼ぶ。返した関数をマイクを閉じたときに 1 回呼ぶ */
export function beginCapture(): () => void {
  captures += 1;
  setType("play-and-record");
  let ended = false;
  return () => {
    if (ended) return;
    ended = true;
    captures = Math.max(0, captures - 1);
    if (captures === 0) setType("auto");
  };
}

/** 読み上げを始める前に呼ぶ。マイクを使っている間は録音できる種類のままにする */
export function preparePlayback(): void {
  if (captures === 0) setType("playback");
}

/** テスト用: いまマイクを開いている数 */
export function activeCaptures(): number {
  return captures;
}
