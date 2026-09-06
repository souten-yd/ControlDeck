/** 長く張りっぱなしにする接続の共通方針。
 *
 * 携帯からの利用が主なので、回線は前提として不安定である。別のアプリへ移って
 * 戻る、電波が変わる、画面を消す——どれでも経路は切れるが、socket は OPEN の
 * まま残ることが多い。iOS では特に顕著で、これを「生きている」と見なすと、
 * 無音に気づくまで何十秒も繋がらないままになる。
 *
 * これまで WebSocket を張る場所ごとに別々の定数と手当てを書いていた。metrics は
 * 直したが Logs は直っていない、といった差が出て、利用者からは「画面によって
 * 挙動が違う」としか見えない。方針をここへ集め、各所はこれを使う。
 *
 * 方針:
 *   1. 生死は readyState ではなく「最後に何か受け取った時刻」で決める
 *   2. 戻ってきたら、疑わしければ捨てて即座に繋ぎ直す
 *   3. 最初の 1 回はすぐ、その後は指数で伸ばすが上限は低く保つ
 *   4. 一瞬の途切れは表示に出さない（すぐ戻るものを壊れて見せない）
 */

/** server が無出力でも送る間隔。terminals は 5 秒、metrics は約 2 秒。
 *  ここは「一番遅い経路」に合わせる。 */
export const HEARTBEAT_MS = 5_000;

/** これだけ黙ったら経路が死んだと見なす。heartbeat 3 回ぶん。 */
export const SILENCE_LIMIT_MS = HEARTBEAT_MS * 3 + 1_000;

/** 復帰時に「まだ生きている」と見なしてよい無音の長さ。heartbeat 2 回ぶん。 */
export const RETURN_GRACE_MS = HEARTBEAT_MS * 2 + 1_000;

/** 切断を表示に出すまでの猶予。これより短い途切れは見せない。 */
export const UI_GRACE_MS = 2_000;

const FIRST_RETRY_MS = 300;
const MAX_RETRY_MS = 8_000;
/** 復帰の合図が続けて来ても繋ぎ直しを連打しない間隔。 */
const REVIVE_THROTTLE_MS = 3_000;

/** 何回目の再試行を、どれだけ待ってから行うか。
 *
 * 携帯の切断はたいてい一瞬なので、最初はすぐ試す。駄目なときだけ伸ばし、
 * 上限は低く保つ。上限が高いと、回線が戻っているのに待たされる。
 */
export function retryDelayMs(attempt: number): number {
  if (attempt <= 0) return FIRST_RETRY_MS;
  return Math.min(MAX_RETRY_MS, 1_000 * 2 ** (attempt - 1));
}

/** 直前まで受信できていたか。readyState は見ない。 */
export function looksAlive(lastMessageAt: number, now = Date.now()): boolean {
  return now - lastMessageAt < RETURN_GRACE_MS;
}

/** 黙りすぎて死んだと見なせるか。 */
export function looksDead(lastMessageAt: number, now = Date.now()): boolean {
  return now - lastMessageAt >= SILENCE_LIMIT_MS;
}

/**
 * 画面へ戻ってきたとき・回線が戻ったときに繋ぎ直す。
 *
 * `revive` は「疑わしければ捨てて繋ぎ直す」処理。呼ぶ側が socket の後始末まで
 * 面倒を見る。返り値を呼ぶと購読を解除する。
 */
export function watchForReturn(revive: () => void): () => void {
  let lastAt = 0;
  const handle = () => {
    if (document.visibilityState !== "visible") return;
    const now = Date.now();
    if (now - lastAt < REVIVE_THROTTLE_MS) return;
    lastAt = now;
    revive();
  };
  document.addEventListener("visibilitychange", handle);
  window.addEventListener("online", handle);
  window.addEventListener("pageshow", handle);
  window.addEventListener("focus", handle);
  return () => {
    document.removeEventListener("visibilitychange", handle);
    window.removeEventListener("online", handle);
    window.removeEventListener("pageshow", handle);
    window.removeEventListener("focus", handle);
  };
}

/** 再接続を予約させずに socket を捨てる。捨てた側の onclose で二重に繋ぎに行かない。 */
export function discardSocket(socket: WebSocket | null): void {
  if (!socket) return;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  try {
    socket.close();
  } catch {
    /* 既に閉じている */
  }
}
