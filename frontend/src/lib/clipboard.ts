/** 文字列をクリップボードへ。navigator.clipboard が使えない（http など）ときは一時 textarea で写す。 */
export async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 下の方法を試す
  }
  // http（secure context でない）では一時 textarea で写す。select() でフォーカスと選択が
  // 移るので、終わったら元の要素と選択へ戻す（戻さないと次に打った文字がターミナルに届かない）。
  const previousFocus = document.activeElement as HTMLElement | null;
  const selection = window.getSelection();
  const previousRanges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i)) : [];
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  if (selection) {
    selection.removeAllRanges();
    previousRanges.forEach((range) => selection.addRange(range));
  }
  previousFocus?.focus?.({ preventScroll: true });
  return ok;
}
