import { useCallback, useEffect, useRef, useState } from "react";

/** つまんで並べ替える。
 *
 * HTML5 の drag and drop は touch で発火しないので、pointer event で組む。
 * ControlDeck は携帯から使う画面が主なので、touch で動かないものは入れない意味がない。
 *
 * つまんでいる間だけ全体の選択とスクロールを止める。止めないと、指で動かすと
 * カードではなくページが動く。
 */
export function useDragReorder(
  ids: string[],
  onMove: (id: string, to: number) => void,
): {
  dragging: string | null;
  over: number | null;
  handleProps: (id: string) => {
    onPointerDown: (event: React.PointerEvent) => void;
    style: React.CSSProperties;
  };
  rowRef: (id: string) => (node: HTMLElement | null) => void;
} {
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const rows = useRef(new Map<string, HTMLElement>());
  const state = useRef<{ id: string; to: number } | null>(null);

  const rowRef = useCallback(
    (id: string) => (node: HTMLElement | null) => {
      if (node) rows.current.set(id, node);
      else rows.current.delete(id);
    },
    [],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMoveEvent = (event: PointerEvent) => {
      let index = 0;
      for (const [, node] of rows.current) {
        const box = node.getBoundingClientRect();
        if (event.clientY > box.top + box.height / 2) index += 1;
      }
      const bounded = Math.max(0, Math.min(ids.length - 1, index));
      setOver(bounded);
      if (state.current) state.current.to = bounded;
    };
    const finish = () => {
      const held = state.current;
      state.current = null;
      setDragging(null);
      setOver(null);
      if (held) onMove(held.id, held.to);
    };
    // passive: false でないと、touch のスクロールを止められない。
    window.addEventListener("pointermove", onMoveEvent, { passive: false });
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    const previous = document.body.style.touchAction;
    document.body.style.touchAction = "none";
    return () => {
      window.removeEventListener("pointermove", onMoveEvent);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      document.body.style.touchAction = previous;
    };
  }, [dragging, ids.length, onMove]);

  const handleProps = useCallback(
    (id: string) => ({
      onPointerDown: (event: React.PointerEvent) => {
        // 左ボタン以外と、つまみ以外からの発火は無視する。
        if (event.button !== 0) return;
        event.preventDefault();
        state.current = { id, to: ids.indexOf(id) };
        setDragging(id);
        setOver(ids.indexOf(id));
      },
      style: { touchAction: "none" as const },
    }),
    [ids],
  );

  return { dragging, over, handleProps, rowRef };
}
