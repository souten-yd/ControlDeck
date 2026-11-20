/** 公報・論文を特許の分野へ取り込むシート。 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { patentsApi } from "../../api/patents";
import { useToasts } from "../../stores";
import { BottomSheet, primaryButtonClass } from "../../components/ui";
import { errorText, input } from "./shared";

export function LiteratureSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [isPublic, setPublic] = useState(false);
  const add = useMutation({
    mutationFn: () => patentsApi.addLiterature({ title, text, url, public: isPublic }),
    onSuccess: () => { show("取り込みを始めました（Jobs で進み具合を見られます）"); onClose(); },
    onError: (e) => show(errorText(e), "error"),
  });
  return (
    <BottomSheet title="公報・論文を取り込む" onClose={onClose}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className={input} placeholder="題名・公報番号" value={title} onChange={(e) => setTitle(e.target.value)} />
        <input className={input} placeholder="URL（本文を貼る場合は空のまま）" value={url} onChange={(e) => setUrl(e.target.value)} />
        <textarea className={`${input} min-h-32`} placeholder="本文" value={text} onChange={(e) => setText(e.target.value)} />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={isPublic} onChange={(e) => setPublic(e.target.checked)} />
          <span>公開済みの公報・論文（クラウドのモデルの横断検索にも出してよい）。社内資料・未公開の内容なら外したまま</span>
        </label>
        <button className={`${primaryButtonClass} w-full`} disabled={(!text.trim() && !url.trim()) || add.isPending}>取り込む</button>
      </form>
    </BottomSheet>
  );
}
