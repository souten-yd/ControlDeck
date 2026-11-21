/** 公開データの取得先（EPO・USPTO・OpenAlex・RSS・GDELT）。1 回だけ取得するシートと、鍵の設定のシート。
 *  検索語は外部の検索サービスへ送られるので、送る前に「未公開の発明の内容を含まない」ことを必ず確かめてもらう
 *  （2026-11-21 R3-2。backend/app/knowledge/sources.py と patents/router.py「公開データの取得・ウォッチ」）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { patentsApi, type PatentSource } from "../../api/patents";
import { useToasts } from "../../stores";
import { BottomSheet, Skeleton, primaryButtonClass } from "../../components/ui";
import { errorText, input } from "./shared";

export const KEY_LABEL: Record<string, string> = {
  jpo_token_url: "トークン取得の URL（利用登録の後に特許庁から通知）", jpo_id: "ID（特許庁から通知）", jpo_password: "パスワード（特許庁から通知）",
  epo_key: "EPO Consumer Key", epo_secret: "EPO Consumer Secret", uspto_key: "USPTO API キー",
};

export function useSources() {
  return useQuery({ queryKey: ["patent-sources"], queryFn: patentsApi.sources, staleTime: 30_000 });
}

/** 外部へ送る前の確認。チェックしないと送れない。 */
export function PublicConsent({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3 rounded-xl bg-amber-50 p-3 text-sm leading-relaxed text-amber-900 dark:bg-amber-900/20 dark:text-amber-100">
      <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-accent-600" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>この検索語は外部の検索サービスへ送られます。未公開の発明の内容を含まないことを確認しました</span>
    </label>
  );
}

/** 取得先の選択。鍵の無いものは選べない。 */
export function SourceSelect({ sources, value, onChange }: { sources: PatentSource[]; value: string; onChange: (v: string) => void }) {
  return (
    <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
      {sources.map((s) => (
        <option key={s.id} value={s.id} disabled={!s.ready}>{s.label}{s.ready ? "" : "（鍵が未設定）"}</option>
      ))}
    </select>
  );
}

export function firstReady(sources: PatentSource[] | undefined) {
  return sources?.find((s) => s.ready)?.id ?? "";
}

export function FetchSheet({ onClose }: { onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const { data } = useSources();
  const sources = data?.sources ?? [];
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(25);
  const [consent, setConsent] = useState(false);
  const chosen = sources.find((s) => s.id === (source || firstReady(sources)));
  const fetch = useMutation({
    mutationFn: () => patentsApi.fetchSource(chosen!.id, { query: query.trim(), limit, approved_public: true }),
    onSuccess: () => { show("取得を始めました（Jobs で進み具合を見られます）"); onClose(); },
    onError: (e) => show(errorText(e), "error"),
  });
  const ok = Boolean(chosen?.ready && query.trim() && consent && limit >= 1 && limit <= 100);
  return (
    <BottomSheet title="公開データを取得" onClose={onClose}>
      {!data ? <Skeleton className="h-40" /> : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (ok) fetch.mutate(); }}>
          <label className="block text-xs font-medium text-zinc-500">取得先
            <div className="mt-1"><SourceSelect sources={sources} value={chosen?.id ?? ""} onChange={setSource} /></div>
          </label>
          <label className="block text-xs font-medium text-zinc-500">検索語
            <input className={`${input} mt-1`} value={query} maxLength={500} placeholder={chosen?.hint} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="block text-xs font-medium text-zinc-500">件数（最大 100）
            <input className={`${input} mt-1 w-28`} type="number" min={1} max={100} inputMode="numeric" value={limit}
              onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          <PublicConsent checked={consent} onChange={setConsent} />
          <button className={`${primaryButtonClass} w-full`} disabled={!ok || fetch.isPending}>取得する</button>
          <p className="text-xs text-zinc-500">取得した公報・論文は「公報」と分析に入ります。</p>
        </form>
      )}
    </BottomSheet>
  );
}

/** 取得先の鍵。保存した値は表示しない（「設定済み」だけ）。空欄の項目は変えない。 */
export function SourceKeysSheet({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useSources();
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => patentsApi.setSourceKeys(Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()))),
    onSuccess: (next) => { qc.setQueryData(["patent-sources"], next); setValues({}); show("保存しました"); },
    onError: (e) => show(errorText(e), "error"),
  });
  const keyed = (data?.sources ?? []).filter((s) => s.keys.length > 0);
  const changed = Object.values(values).some((v) => v.trim());
  return (
    <BottomSheet title="取得先の設定" onClose={onClose}>
      {!data ? <Skeleton className="h-40" /> : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (changed) save.mutate(); }} autoComplete="off">
          <p className="text-xs leading-relaxed text-zinc-500">鍵が要る取得先だけです。OpenAlex・RSS・GDELT は鍵なしで使えます。保存した鍵はこの PC に暗号化して置き、画面には出しません。</p>
          {keyed.map((s) => (
            <section key={s.id} className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
              <div className="flex items-center gap-2">
                <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">{s.label}</h3>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${s.ready ? "bg-accent-600/15 text-accent-700 dark:text-accent-300" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}>
                  {s.ready ? "設定済み" : "未設定"}
                </span>
              </div>
              {s.keys.map((k) => (
                <label key={k} className="block text-xs font-medium text-zinc-500">{KEY_LABEL[k] ?? k}
                  <input className={`${input} mt-1`} type={k.endsWith("_url") ? "url" : "password"} autoComplete="new-password" value={values[k] ?? ""}
                    placeholder={s.ready ? "設定済み（変えるときだけ入れる）" : ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
                </label>
              ))}
              {s.signup && (
                <a className="inline-flex min-h-11 items-center text-xs text-accent-700 hover:underline dark:text-accent-300" href={s.signup} target="_blank" rel="noreferrer">
                  {s.id === "jpo" ? "利用登録の案内（特許庁）" : `鍵を取得する（${new URL(s.signup).host}）`}
                </a>
              )}
            </section>
          ))}
          <button className={`${primaryButtonClass} w-full`} disabled={!changed || save.isPending}>保存</button>
        </form>
      )}
    </BottomSheet>
  );
}
