import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "control-deck:terminal-session-order";

function readStored(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeStored(order: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
  } catch {
    /* 並び順は保存できなくても使える */
  }
}

/** 並べ替えた順番を覚える。
 *
 * 順番は表示の好みなので、サーバーへは送らずこの browser に閉じる。知らない id は
 * 後ろへ回し、消えた session は落とす。そうしないと、別の端末で作った session が
 * 先頭に来たり、終了した session のぶんだけ順番が歪んだりする。
 */
export function useSessionOrder<T extends { id: string }>(sessions: T[]): {
  ordered: T[];
  move: (id: string, to: number) => void;
} {
  const [order, setOrder] = useState<string[]>(readStored);
  const present = sessions.map((session) => session.id);
  const signature = present.join(" ");

  useEffect(() => {
    const ids = signature ? signature.split(" ") : [];
    setOrder((current) => {
      const kept = current.filter((id) => ids.includes(id));
      const added = ids.filter((id) => !kept.includes(id));
      const next = [...kept, ...added];
      if (next.length === current.length && next.every((id, index) => id === current[index])) return current;
      writeStored(next);
      return next;
    });
    // 顔ぶれが変わったときだけ整える。毎 render で走らせると更新が止まらない。
  }, [signature]);

  const rank = new Map(order.map((id, index) => [id, index]));
  const ordered = [...sessions].sort(
    (a, b) => (rank.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.id) ?? Number.MAX_SAFE_INTEGER),
  );

  const move = useCallback((id: string, to: number) => {
    setOrder((current) => {
      const from = current.indexOf(id);
      if (from < 0) return current;
      const bounded = Math.max(0, Math.min(current.length - 1, to));
      if (bounded === from) return current;
      const next = [...current];
      next.splice(from, 1);
      next.splice(bounded, 0, id);
      writeStored(next);
      return next;
    });
  }, []);

  return { ordered, move };
}
