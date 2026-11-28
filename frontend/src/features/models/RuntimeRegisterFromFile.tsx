/** 全ランタイム共通の「ファイルから登録」。
 *
 *  選べるルートは /data1tb と /data2tb だけ（commander の workdir roots を使い、
 *  files API の一覧でファイルを開く）。backend の inspect が realpath で再検証する。
 *  名前（alias）はファイル名から自動で作り、形式・量子化・上限はファイルから読む
 *  （GGUF のヘッダ・config.json）。細かいメタデータはエキスパートの中だけに出す。
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePicker, type FilePickerSource } from "../../components/FilePicker";
import { Skeleton } from "../../components/ui";
import { useToasts } from "../../stores";
import { useExpert } from "../commander/TeamRoles";
import { api } from "../../api/client";
import { inspectRegisterPath, type RegisterInspect } from "../../api/models";
import { invalidateRuntimeQueries } from "./runtimeSettings";

/** ルート一覧だけは 2 つに統一した API から。一覧・作成は files API（path 検証つき）。 */
export const MODEL_FILE_PICKER_SOURCE: FilePickerSource = {
  rootsUrl: "/commander/workdir/roots",
  listUrl: "/files/list",
  mkdirUrl: "/files/directory",
};

export interface RuntimeRegisterFromFileProps {
  /** 登録先の runtime id（llama / radiance / sly-radiance / strata / maya）。 */
  runtime: string;
  mode?: "file" | "dir";
  initialPath?: string;
  /** 追加で保存 API に渡す欄（Strata の family/model、Maya の setup_id など）。 */
  extra?: Record<string, unknown>;
  /** 独自の保存処理。省略時は inspect が返した register_endpoint へ POST する。 */
  onRegistered?: (info: RegisterInspect, alias: string) => void | Promise<void>;
  disabled?: boolean;
  expert?: boolean;
}

const gb = (bytes: number) => bytes >= 1e9
  ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(0)} MB`;
const viewName = (runtime: string) => runtime === "llama" ? "llama.cpp" : runtime;

export function RuntimeRegisterFromFile({
  runtime, mode = "file", initialPath, extra, onRegistered, disabled = false, expert: expertProp,
}: RuntimeRegisterFromFileProps) {
  const storedExpert = useExpert();
  const expert = expertProp ?? storedExpert;
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [path, setPath] = useState(initialPath ?? "");
  const [alias, setAlias] = useState("");
  const [aliasEdited, setAliasEdited] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data, error, isFetching } = useQuery({
    queryKey: ["register-inspect", path],
    queryFn: () => inspectRegisterPath(path),
    enabled: path.trim().length > 0,
    retry: false,
  });

  useEffect(() => {
    if (data?.alias_suggestion && !aliasEdited) setAlias(data.alias_suggestion);
  }, [data?.alias_suggestion, aliasEdited]);

  const view = data?.runtimes.find((item) => item.runtime === viewName(runtime) || item.runtime === runtime);
  const meta = data?.metadata ?? {};
  // Maya の登録は setup_id + folder が必須の別 API。Strata は family + model が要る。
  // それらを渡さないまま既定の POST を叩くと 422 になるので、その場合は親の保存処理を要求する。
  const needsCustomSave = runtime === "maya" && !onRegistered
    || runtime === "strata" && !onRegistered && (!extra?.family || !extra?.model);
  const canRegister = !!view?.eligible && !!alias.trim() && !disabled && !saving && !needsCustomSave;

  const register = async () => {
    if (!data || !view) return;
    setSaving(true);
    try {
      if (onRegistered) {
        await onRegistered(data, alias.trim());
      } else {
        // inspect は完全な API パス（/api/v1/...）を返す。api() が前置するのでここで外す。
        await api(view.register_endpoint.replace(/^\/api\/v1/, ""), {
          method: "POST",
          json: { alias: alias.trim(), model_path: data.path, ...(extra ?? {}) },
        });
      }
      await Promise.all(invalidateRuntimeQueries(qc, runtime));
      show("登録しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "登録できませんでした", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2" data-runtime-register-file={runtime} data-expert={expert ? "on" : "off"}>
      <div className="flex items-center gap-2">
        <button type="button" disabled={disabled} onClick={() => setPicking(true)}
          className="min-h-11 flex-1 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">
          {mode === "file" ? "モデルのファイルを選ぶ" : "モデルのフォルダを選ぶ"}
        </button>
        {path && <button type="button" onClick={() => { setPath(""); setAlias(""); setAliasEdited(false); }}
          className="min-h-11 px-2 text-xs text-zinc-500">選び直す</button>}
      </div>
      {path && <p className="min-w-0 truncate font-mono text-[11px] text-zinc-500" title={path}>{path}</p>}
      {isFetching && path && <Skeleton className="h-16" />}
      {error && <p className="text-xs text-red-600">{error instanceof Error ? error.message : "読み取れませんでした"}</p>}

      {data && (
        <div className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <label className="block text-xs">モデル名（ファイル名から自動）
            <input aria-label="登録するモデル名" value={alias} disabled={disabled || saving}
              onChange={(e) => { setAlias(e.target.value); setAliasEdited(true); }}
              className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 font-mono text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          </label>
          <p className="num text-[11px] text-zinc-500">
            {data.format} · {gb(data.size_bytes)}
            {typeof meta.quantization === "string" && ` · ${meta.quantization}`}
            {typeof meta.context_length === "number" && ` · 上限 ${meta.context_length.toLocaleString()} トークン`}
          </p>
          {view && !view.eligible && (
            <p className="text-[11px] text-amber-700 dark:text-amber-300">{view.reason || "このランタイムには登録できません"}</p>
          )}
          {view?.eligible && view.reason && (
            <p className="text-[11px] text-zinc-500">{view.reason}</p>
          )}
          {needsCustomSave && (
            <p className="text-[11px] text-amber-700 dark:text-amber-300">
              Maya・Strata は専用の登録画面（または onRegistered）から保存してください
            </p>
          )}
          {expert && (
            <div className="space-y-1 border-t border-zinc-100 pt-2 dark:border-zinc-800">
              <p className="text-[11px] text-zinc-500">{data.kind} · {data.path}</p>
              {Object.entries(data.metadata).map(([key, value]) => (
                <p key={key} className="truncate font-mono text-[10px] text-zinc-400" title={`${key}=${String(value)}`}>
                  {key}={String(value)}
                </p>
              ))}
              {data.runtimes.filter((item) => item.runtime !== viewName(runtime) && item.runtime !== runtime)
                .map((item) => <p key={item.runtime} className="text-[10px] text-zinc-400">
                  {item.runtime}: {item.eligible ? "登録可" : item.reason}
                </p>)}
            </div>
          )}
          <button type="button" disabled={!canRegister} onClick={() => void register()}
            className="min-h-11 w-full rounded-xl bg-accent-600 px-4 text-sm font-medium text-white disabled:opacity-40">
            {saving ? "登録中…" : "登録する"}
          </button>
        </div>
      )}

      {picking && <FilePicker source={MODEL_FILE_PICKER_SOURCE} mode={mode}
        title={mode === "file" ? "モデルのファイルを選択（/data1tb・/data2tb）" : "モデルのフォルダを選択（/data1tb・/data2tb）"}
        initialPath={path || undefined}
        onSelect={(selected) => {
          // picker のルート一覧と別に、選んだ値も 2 ルートの中に閉じる（defense in depth）。
          if (!/^\/data(1|2)tb\//.test(selected) && selected !== "/data1tb" && selected !== "/data2tb") {
            show("選べるのは /data1tb と /data2tb の中だけです", "error");
            return;
          }
          setPath(selected); setAlias(""); setAliasEdited(false); setPicking(false);
        }}
        onClose={() => setPicking(false)} />}
    </div>
  );
}
