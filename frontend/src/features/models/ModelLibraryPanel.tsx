/** モデルライブラリ: モデルの保存先を複数ドライブに持ち、未登録GGUFをその場で登録する。
 *
 * ドライブはマウント名ではなく uuid で参照するため、マウント名を変えても追従する。
 * M.2 の物理スロット番号はソフトウェアから取得できないので、UI は
 * 「接続方式 + モデル名 + 容量」でドライブを識別させる。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listModelLibraries, listStorageVolumes, saveModelLibraries, scanModelLibrary,
  type ModelLibrary, type ModelLibraryInput, type StorageVolume,
} from "../../api/models";
import { useAuth, useToasts } from "../../stores";
import { ConfirmDialog, Skeleton } from "../../components/ui";
import { IconPlus, IconTrash } from "../../components/icons";

function gb(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(0)} MB`;
}

function volumeLabel(v: StorageVolume): string {
  const kind = v.transport ? v.transport.toUpperCase() : (v.rotational ? "HDD" : "SSD");
  return `${v.model || v.device} · ${kind} · ${gb(v.total_bytes)}（空き ${gb(v.free_bytes)}）`;
}

/** 使用量バー。空きが少ないほど警告色にする。 */
function UsageBar({ total, free }: { total: number; free: number }) {
  const used = Math.max(0, total - free);
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const tone = pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-500" : "bg-accent-600";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ModelLibraryPanel() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("workflows.edit");
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<ModelLibrary | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data: libraries, isLoading } = useQuery({
    queryKey: ["model-libraries"], queryFn: listModelLibraries,
  });
  const { data: volumes } = useQuery({
    queryKey: ["storage-volumes"], queryFn: listStorageVolumes, enabled: canEdit,
  });

  const save = useMutation({
    mutationFn: (next: ModelLibraryInput[]) => saveModelLibraries(next),
    onSuccess: () => {
      show("モデルの保存場所を更新しました");
      qc.invalidateQueries({ queryKey: ["model-libraries"] });
      setAdding(false); setRemoving(null);
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });

  const toInput = (list: ModelLibrary[]): ModelLibraryInput[] => list.map((l) => ({
    id: l.id, label: l.label, volume_uuid: l.volume_uuid,
    subpath: l.subpath, path: l.path, default: l.default,
  }));

  if (isLoading || !libraries) return <Skeleton className="h-32" />;

  const makeDefault = (id: string) =>
    save.mutate(toInput(libraries).map((l) => ({ ...l, default: l.id === id })));

  return (
    <div className="space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-zinc-500">モデルの保存場所</p>
          <p className="mt-0.5 text-[10px] text-zinc-400">
            ダウンロード先とスキャン対象になります。ドライブは UUID で記録するため、
            マウント名を変えても追従します。
          </p>
        </div>
        {canEdit && !adding && (
          <button onClick={() => setAdding(true)}
            className="flex shrink-0 items-center gap-1 rounded-xl bg-zinc-100 px-2.5 py-1.5 text-[11px] font-medium dark:bg-zinc-800">
            <IconPlus /> 追加
          </button>
        )}
      </div>

      <ul className="space-y-2">
        {libraries.map((lib) => (
          <li key={lib.id} className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 truncate text-sm font-medium">
                  {lib.label}
                  {lib.default && (
                    <span className="rounded bg-accent-100 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/20 dark:text-accent-300">
                      既定
                    </span>
                  )}
                  {!lib.mounted && (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">
                      未接続
                    </span>
                  )}
                </p>
                <p className="num mt-0.5 truncate font-mono text-[10px] text-zinc-400">
                  {lib.mounted ? lib.path : "ドライブが接続されていません"}
                </p>
              </div>
              {canEdit && (
                <div className="flex shrink-0 items-center gap-1">
                  {!lib.default && lib.mounted && (
                    <button onClick={() => makeDefault(lib.id)} disabled={save.isPending}
                      className="rounded-lg px-2 py-1 text-[10px] text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                      既定にする
                    </button>
                  )}
                  {libraries.length > 1 && (
                    <button onClick={() => setRemoving(lib)} aria-label={`${lib.label}を削除`}
                      className="rounded-lg p-1.5 text-zinc-400 hover:text-red-600">
                      <IconTrash />
                    </button>
                  )}
                </div>
              )}
            </div>

            {lib.mounted && lib.total_bytes !== null && lib.free_bytes !== null && (
              <div className="mt-2 space-y-1">
                <UsageBar total={lib.total_bytes} free={lib.free_bytes} />
                <p className="num text-[10px] text-zinc-400">
                  GGUF {lib.gguf_count} 件 {gb(lib.gguf_bytes)} · 空き {gb(lib.free_bytes)} / {gb(lib.total_bytes)}
                  {lib.orphan_count > 0 && ` · 未登録 ${lib.orphan_count} 件`}
                </p>
              </div>
            )}

            {lib.mounted && lib.gguf_count > 0 && (
              <button type="button"
                onClick={() => setExpanded(expanded === lib.id ? null : lib.id)}
                className="mt-1.5 text-[11px] font-medium text-accent-600 dark:text-accent-400">
                {expanded === lib.id ? "▾ ファイルを隠す" : `▸ ファイルを見る（${lib.gguf_count}）`}
              </button>
            )}
            {expanded === lib.id && <LibraryFiles libraryId={lib.id} />}
          </li>
        ))}
      </ul>

      {adding && volumes && (
        <AddLibraryForm
          volumes={volumes}
          existingIds={libraries.map((l) => l.id)}
          busy={save.isPending}
          onCancel={() => setAdding(false)}
          onAdd={(entry) => save.mutate([...toInput(libraries), entry])}
        />
      )}

      {removing && (
        <ConfirmDialog
          title={`「${removing.label}」を保存場所から外しますか？`}
          message="一覧から外すだけで、ディスク上のモデルファイルは削除しません。"
          confirmLabel="外す"
          busy={save.isPending}
          onConfirm={() => save.mutate(toInput(libraries.filter((l) => l.id !== removing.id)))}
          onClose={() => setRemoving(null)}
        />
      )}
    </div>
  );
}

/** ライブラリ内の GGUF 一覧。未登録のものはその場で llama.cpp モデルとして登録できる。 */
function LibraryFiles({ libraryId }: { libraryId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["model-library-scan", libraryId],
    queryFn: () => scanModelLibrary(libraryId),
  });
  if (isLoading) return <Skeleton className="mt-2 h-16" />;
  if (!data || data.files.length === 0) {
    return <p className="mt-2 text-[10px] text-zinc-400">GGUF が見つかりません。</p>;
  }
  return (
    <ul className="mt-2 space-y-1 border-t border-zinc-100 pt-2 dark:border-zinc-800">
      {data.files.map((f) => (
        <li key={f.path} className="flex items-center gap-2 py-0.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${f.registered ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`}
            title={f.registered ? "登録済み" : "未登録"} />
          <span className="min-w-0 flex-1 truncate text-[11px]">{f.name}</span>
          <span className="num shrink-0 text-[10px] text-zinc-400">{gb(f.size)}</span>
          <span className="shrink-0 text-[10px] text-zinc-400">
            {f.registered ? f.used_by.join(", ") : "未登録"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AddLibraryForm({ volumes, existingIds, busy, onAdd, onCancel }: {
  volumes: StorageVolume[];
  existingIds: string[];
  busy: boolean;
  onAdd: (entry: ModelLibraryInput) => void;
  onCancel: () => void;
}) {
  const [uuid, setUuid] = useState(volumes.find((v) => !v.is_system)?.uuid ?? volumes[0]?.uuid ?? "");
  const [subpath, setSubpath] = useState("LLM");
  const [label, setLabel] = useState("");
  const selected = volumes.find((v) => v.uuid === uuid);
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  const submit = () => {
    if (!selected) return;
    let id = (subpath || selected.transport || "lib").replace(/[^A-Za-z0-9._-]+/g, "-").toLowerCase();
    while (existingIds.includes(id)) id = `${id}-2`;
    onAdd({
      id, label: label.trim() || `${selected.model || selected.device}`,
      volume_uuid: uuid, subpath, path: "", default: false,
    });
  };

  return (
    <div className="space-y-2.5 rounded-xl border border-dashed border-zinc-300 p-3 dark:border-zinc-600">
      <p className="text-xs font-semibold text-zinc-500">保存場所を追加</p>
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-zinc-500">ドライブ</span>
        <select value={uuid} onChange={(e) => setUuid(e.target.value)} className={input}>
          {volumes.map((v) => (
            <option key={v.uuid} value={v.uuid}>
              {volumeLabel(v)}{v.is_system ? " ※システムドライブ" : ""}
            </option>
          ))}
        </select>
      </label>
      {selected?.is_system && (
        <p className="rounded-lg bg-amber-50 px-2.5 py-2 text-[10px] leading-relaxed text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
          システムドライブです。モデルで埋めるとOSの動作に影響するため、別のドライブを推奨します。
        </p>
      )}
      {selected && !selected.writable && (
        <p className="rounded-lg bg-amber-50 px-2.5 py-2 text-[10px] leading-relaxed text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
          このドライブの直下には書き込めません。書き込み可能なサブフォルダを指定してください。
        </p>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-zinc-500">サブフォルダ</span>
          <input value={subpath} onChange={(e) => setSubpath(e.target.value)} placeholder="LLM"
            className={`${input} font-mono text-xs`} />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-zinc-500">表示名（任意）</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={selected?.model ?? ""}
            className={input} />
        </label>
      </div>
      <p className="num font-mono text-[10px] text-zinc-400">
        保存先: {selected ? `${selected.mountpoint}${subpath ? `/${subpath}` : ""}` : "—"}
      </p>
      <p className="text-[10px] leading-relaxed text-zinc-400">
        このパスが config.yaml の files.allowed_roots の外にある場合は保存できません。
        必要なら allowed_roots に追加してください。
      </p>
      <div className="flex gap-2">
        <button onClick={submit} disabled={busy || !selected}
          className="flex-1 rounded-xl bg-accent-600 py-2 text-xs font-medium text-white disabled:opacity-40">
          追加
        </button>
        <button onClick={onCancel} className="rounded-xl px-3 py-2 text-xs text-zinc-500">キャンセル</button>
      </div>
    </div>
  );
}
