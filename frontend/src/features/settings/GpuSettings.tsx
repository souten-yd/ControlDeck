/** 設定の「GPU 設定」。
 *
 * 2026-11-18 利用者「電力制限 210 W は有効のまま、ホットスポット温度を下げてファンの回転数を下げたい。
 * 性能低下は程々に」。コア周波数と電圧のオフセットを別々に試し、設定値と実測を分けて見られるようにする。
 * 値は「適用」を押したときだけ書き込み、起動時には自動で適用しない。電力・静音は Models の共通設定から移した。 */
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConfirmDialog, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

interface OdItem { supported: boolean; unit: string; current: number | null; min: number | null; max: number | null }
interface GpuTuning {
  available: boolean; message?: string; bdf?: string; xfx?: boolean; helper_ready?: boolean;
  items?: { core: OdItem; voltage: OdItem };
  previous?: { core?: number; voltage?: number };
  preset: { label: string; core: number; voltage: number };
  boot?: { enabled: boolean; core: number | null; voltage: number | null; state: string; message: string };
  live?: { core_mhz: number | null; power_w: number | null; power_limit_w: number | null; hotspot_c: number | null; fan_rpm: number | null; busy_percent: number | null };
}
interface AmdGpuPolicy {
  enabled: boolean; profile: "quiet" | "balanced" | "full" | "custom"; power_limit_watts: number;
  memory_clock_mode: "auto" | "minimum" | "limit"; memory_clock_level: number;
  core_clock_mode: "auto" | "limit"; core_clock_level: number;
}
type ClockLevel = { level: number; mhz: number; current: boolean };
interface AmdGpuEnv {
  bdf: string; helper_installed: boolean;
  power: { current_watts: number; min_watts: number; max_watts: number; default_watts: number };
  memory: { supported: boolean; levels: ClockLevel[] }; core: { supported: boolean; levels: ClockLevel[] };
  presets: Record<"quiet" | "balanced" | "full", Partial<AmdGpuPolicy>> & { balanced: { power_limit_watts: number } };
}
interface RuntimeEnvironment { amd_gpu: AmdGpuEnv | null; policy: { amd_gpu: AmdGpuPolicy } }

function L({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>{children}</label>;
}

const signed = (value: number | null | undefined, unit: string) => value == null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value)} ${unit}`;
const reading = (value: number | null | undefined, unit: string) => value == null ? "—" : `${Math.round(value).toLocaleString()} ${unit}`;

function OffsetField({ label, item, value, onChange }: { label: string; item?: OdItem; value: string; onChange: (v: string) => void }) {
  if (!item?.supported) return <div className="rounded-xl border border-zinc-200 p-3 text-xs text-zinc-500 dark:border-zinc-800">{label}: ドライバが公開していません</div>;
  return (
    <label className="block rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
      <span className="flex flex-wrap items-baseline justify-between gap-x-2 text-sm font-medium">{label}<span className="text-xs font-normal text-zinc-500">いま {signed(item.current, item.unit)}</span></span>
      <span className="mt-2 flex items-center gap-2">
        <input type="number" inputMode="numeric" min={item.min ?? undefined} max={item.max ?? undefined} step={item.unit === "MHz" ? 50 : 5} value={value}
          onChange={(e) => onChange(e.target.value)} placeholder="変更しない"
          className="min-h-11 w-full min-w-0 rounded-lg border border-zinc-300 bg-white px-3 text-base tabular-nums dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm" />
        <span className="shrink-0 text-sm text-zinc-500">{item.unit}</span>
      </span>
      <span className="mt-1 block text-xs text-zinc-500">指定できる範囲: {signed(item.min, item.unit)} 〜 {signed(item.max, item.unit)}</span>
    </label>
  );
}

function TuningPanel() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const canManage = useAuth((s) => s.can)("settings.manage");
  const { data } = useQuery({ queryKey: ["gpu-tuning"], queryFn: () => api<GpuTuning>("/system/gpu-tuning"), refetchInterval: 2000 });
  const [core, setCore] = useState("");
  const [voltage, setVoltage] = useState("");
  const [confirming, setConfirming] = useState(false);
  const done = (next: GpuTuning, message: string) => { qc.setQueryData(["gpu-tuning"], next); setCore(""); setVoltage(""); show(message); };
  const failed = (e: unknown) => { void qc.invalidateQueries({ queryKey: ["gpu-tuning"] }); show(e instanceof Error ? e.message : "適用できませんでした", "error"); };
  const apply = useMutation({
    mutationFn: () => api<GpuTuning>("/system/gpu-tuning/apply", { method: "POST",
      json: { core_offset_mhz: core.trim() === "" ? null : Number(core), voltage_offset_mv: voltage.trim() === "" ? null : Number(voltage) } }),
    onSuccess: (next) => done(next, "適用し、ドライバから読み戻して確認しました"), onError: failed, onSettled: () => setConfirming(false),
  });
  const restore = useMutation({
    mutationFn: () => api<GpuTuning>("/system/gpu-tuning/restore", { method: "POST" }),
    onSuccess: (next) => done(next, "適用前の値に戻しました"), onError: failed,
  });
  const boot = useMutation({
    mutationFn: (enabled: boolean) => api<GpuTuning>("/system/gpu-tuning/boot", { method: "POST", json: { enabled } }),
    onSuccess: (next) => { qc.setQueryData(["gpu-tuning"], next); show(next.boot?.enabled ? "次の起動から自動で適用します" : "自動適用をやめました"); },
    onError: failed,
  });
  if (!data) return null;
  const previous = Object.entries(data.previous ?? {});
  const valid = (text: string) => text.trim() === "" || Number.isInteger(Number(text));
  const canApply = canManage && data.helper_ready && (core.trim() !== "" || voltage.trim() !== "") && valid(core) && valid(voltage) && !apply.isPending;
  const live = data.live;
  return (
    <section data-gpu-tuning className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div>
        <h3 className="text-sm font-semibold">発熱を抑える（コア周波数・電圧）</h3>
        <p className="mt-1 text-xs leading-relaxed text-zinc-500">まず周波数だけを少しずつ下げ、効果を見てから電圧を試します。再起動すると元に戻ります（下の自動適用を有効にしたときを除く）。</p>
      </div>
      {!data.available ? <p className="text-sm text-amber-700 dark:text-amber-300">{data.message}</p> : <>
        <p className="text-xs text-zinc-500">対象: {data.bdf}{data.xfx ? "（XFX Radeon AI PRO R9700）" : ""}</p>
        <div className="grid gap-2 sm:grid-cols-2">
          <OffsetField label="最大コア周波数オフセット" item={data.items?.core} value={core} onChange={setCore} />
          <OffsetField label="コア電圧オフセット" item={data.items?.voltage} value={voltage} onChange={setVoltage} />
        </div>
        {!data.helper_ready && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">書き込みの権限がまだありません。サーバーで <code>./deck.sh service</code> を実行してください。</p>}
        <div className="flex flex-wrap gap-2">
          {data.xfx && <button type="button" className={secondaryButtonClass} onClick={() => {
            if (data.items?.core.supported) setCore(String(data.preset.core));
            if (data.items?.voltage.supported) setVoltage(String(data.preset.voltage));
          }}>{data.preset.label}</button>}
          <button type="button" className={primaryButtonClass} disabled={!canApply} onClick={() => setConfirming(true)}>適用</button>
          {previous.length > 0 && <button type="button" className={secondaryButtonClass} disabled={!canManage || !data.helper_ready || restore.isPending} onClick={() => restore.mutate()}>適用前の値に戻す</button>}
        </div>
        {previous.length > 0 && <p className="text-xs text-zinc-500">戻す内容: {previous.map(([key, value]) => key === "core" ? `周波数 ${signed(value, "MHz")}` : `電圧 ${signed(value, "mV")}`).join("、")}</p>}
        <label data-gpu-boot className="flex min-h-11 items-start gap-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <input type="checkbox" className="mt-0.5 h-4 w-4" checked={!!data.boot?.enabled} disabled={!canManage || !data.helper_ready || boot.isPending}
            onChange={(e) => boot.mutate(e.target.checked)} />
          <span className="min-w-0 text-sm">再起動後も自動で適用する
            <span className="mt-0.5 block text-xs leading-relaxed text-zinc-500">
              {data.boot?.enabled ? `起動後、ファン制御の開始を待って ${signed(data.boot.core, "MHz")} / ${signed(data.boot.voltage, "mV")} を適用します。` : "いま適用している値を、次の起動から使います。"}
              電力制限とファン制御はそのままです。適用の直後に PC が止まった場合は、次の起動で自動的に無効にします。
            </span>
            {data.boot?.message && <span className="mt-1 block text-xs text-amber-700 dark:text-amber-300">{data.boot.message}</span>}
          </span>
        </label>
        <div>
          <h4 className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">いまの実測（設定値とは別）</h4>
          <dl data-gpu-live className="mt-1.5 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            {([["実コアクロック", reading(live?.core_mhz, "MHz")], ["実消費電力", `${reading(live?.power_w, "W")}${live?.power_limit_w ? ` / 上限 ${Math.round(live.power_limit_w)} W` : ""}`],
              ["ホットスポット", reading(live?.hotspot_c, "°C")], ["ファン", reading(live?.fan_rpm, "rpm")]] as const).map(([label, value]) => (
              <div key={label} className="min-w-0 rounded-lg bg-zinc-50 px-2.5 py-2 dark:bg-white/[0.04]"><dt className="text-xs text-zinc-500">{label}</dt><dd className="mt-0.5 font-medium tabular-nums">{value}</dd></div>
            ))}
          </dl>
        </div>
      </>}
      {confirming && <ConfirmDialog title="GPU に適用" confirmLabel="適用" danger={false} busy={apply.isPending}
        message={`${core.trim() ? `周波数オフセット ${signed(Number(core), "MHz")}　` : ""}${voltage.trim() ? `電圧オフセット ${signed(Number(voltage), "mV")}` : ""}を適用します。電圧を下げすぎると、LLM が止まったり GPU の再起動が必要になることがあります。`}
        onConfirm={() => apply.mutate()} onClose={() => setConfirming(false)} />}
    </section>
  );
}

function PowerPanel() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const canEdit = useAuth((s) => s.can)("workflows.edit");
  const { data } = useQuery({ queryKey: ["runtime-environment"], queryFn: () => api<RuntimeEnvironment>("/models/runtime-environment") });
  const [draft, setDraft] = useState<AmdGpuPolicy | null>(null);
  useEffect(() => { if (data && !draft) setDraft(data.policy.amd_gpu); }, [data, draft]);
  const save = useMutation({
    mutationFn: (gpu: AmdGpuPolicy) => api<RuntimeEnvironment>("/models/runtime-policy", { method: "PUT", json: { amd_gpu: gpu } }),
    onSuccess: (next) => { qc.setQueryData(["runtime-environment"], next); setDraft(next.policy.amd_gpu); show("電力・静音の設定を保存しました"); },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  if (!data?.amd_gpu || !draft) return null;
  return (
    <section data-gpu-power className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div>
        <h3 className="text-sm font-semibold">電力・静音</h3>
        <p className="mt-1 text-xs text-zinc-500">LLM の起動・生成の前に適用します。</p>
      </div>
      <AmdGpuProfilePanel env={data.amd_gpu} gpu={draft} onChange={setDraft} />
      <button type="button" className={primaryButtonClass} disabled={!canEdit || save.isPending} onClick={() => save.mutate(draft)}>{save.isPending ? "保存中…" : "保存"}</button>
    </section>
  );
}

export function GpuSettings() {
  const can = useAuth((s) => s.can);
  return <div className="space-y-4">{can("system.view") && <TuningPanel />}{can("workflows.edit") && <PowerPanel />}</div>;
}

function AmdGpuProfilePanel({ env, gpu, onChange }: {
  env: AmdGpuEnv;
  gpu: AmdGpuPolicy;
  onChange: (next: AmdGpuPolicy) => void;
}) {
  const setGpu = (patch: Partial<AmdGpuPolicy>) => onChange({ ...gpu, ...patch });
  const choose = (profile: "quiet" | "balanced" | "full") => setGpu({ enabled: true, profile, ...env.presets[profile] });
  const labels = { quiet: "静音", balanced: "バランス", full: "フルパワー" } as const;
  return (
    <div className="space-y-2.5 rounded-xl border border-emerald-200 bg-emerald-50/30 p-3 dark:border-emerald-900 dark:bg-emerald-950/20">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">AMD GPU 電力・VRAM静音設定</p>
          <p className="text-[10px] text-zinc-400">{env.bdf} · 現在の電力上限 {env.power.current_watts}W · MCLK {env.memory.levels.find((v) => v.current)?.mhz ?? "N/A"}MHz</p>
        </div>
        <input type="checkbox" checked={gpu.enabled} onChange={(e) => setGpu({ enabled: e.target.checked })} aria-label="AMD GPU設定を有効化" className="h-4 w-4" />
      </div>
      {gpu.enabled && (
        <>
          <div className="grid grid-cols-3 gap-1.5">
            {(Object.keys(labels) as Array<keyof typeof labels>).map((profile) => (
              <button key={profile} type="button" onClick={() => choose(profile)}
                className={`rounded-lg border px-2 py-2 text-xs ${gpu.profile === profile ? "border-accent-500 bg-white font-semibold text-accent-700 dark:bg-zinc-900 dark:text-accent-300" : "border-zinc-200 bg-white/60 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/60"}`}>
                {labels[profile]}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setGpu({ profile: "custom" })} className="text-[11px] font-medium text-accent-600 dark:text-accent-400">カスタム設定</button>
          <p className="rounded-lg bg-white/70 px-2.5 py-2 text-[10px] leading-relaxed text-zinc-500 dark:bg-zinc-900/60">
            {gpu.profile === "quiet" && `静音: ${env.power.min_watts}W・MCLK上限 ${env.memory.levels[Math.max(0, env.memory.levels.length - 2)]?.mhz}MHz（最大から1段だけ低下）。`}
            {gpu.profile === "balanced" && `バランス: ${env.presets.balanced.power_limit_watts}W・MCLK自動。アイドル時は最低周波数へ戻ります。`}
            {gpu.profile === "full" && `フルパワー: 既定${env.power.default_watts}W・MCLK自動。性能優先です。`}
            {gpu.profile === "custom" && "実機が公開する安全範囲内で電力、VRAMクロック、GPUコア上限を個別指定します。"}
          </p>
          {gpu.profile === "custom" && (
            <div className="space-y-2.5">
              <L label={`電力上限 ${gpu.power_limit_watts}W（${env.power.min_watts}〜${env.power.max_watts}W）`}>
                <input type="range" min={env.power.min_watts} max={env.power.max_watts} step={1} value={gpu.power_limit_watts}
                  onChange={(e) => setGpu({ power_limit_watts: Number(e.target.value) })} className="w-full accent-current" />
              </L>
              {env.memory.supported && <L label="VRAMクロック上限（MCLK）">
                <select value={gpu.memory_clock_mode === "auto" ? "auto" : String(gpu.memory_clock_level)}
                  onChange={(e) => e.target.value === "auto" ? setGpu({ memory_clock_mode: "auto", memory_clock_level: 0 }) : setGpu({ memory_clock_mode: "limit", memory_clock_level: Number(e.target.value) })}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="auto">自動（既定・アイドル時は最低へ低下）</option>
                  {env.memory.levels.map((item) => <option key={item.level} value={item.level}>{item.mhz}MHz 以下</option>)}
                </select>
              </L>}
              {env.core.supported && <L label="GPUコアクロック上限（SCLK）">
                <select value={gpu.core_clock_mode === "auto" ? "auto" : String(gpu.core_clock_level)}
                  onChange={(e) => e.target.value === "auto" ? setGpu({ core_clock_mode: "auto", core_clock_level: 0 }) : setGpu({ core_clock_mode: "limit", core_clock_level: Number(e.target.value) })}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="auto">自動（既定）</option>
                  {env.core.levels.filter((item) => item.mhz > 0).map((item) => <option key={item.level} value={item.level}>{item.mhz}MHz 以下</option>)}
                </select>
              </L>}
            </div>
          )}
          {!env.helper_installed && <p className="text-[10px] text-amber-700 dark:text-amber-300">適用helperが未登録です。サーバーで ./deck.sh service を実行すると登録されます。</p>}
          <p className="text-[10px] text-zinc-400">設定はサーバーへ保存され、Control Deck経由のチャット・ワークフロー・手動/自動モデル起動前に適用されます。</p>
        </>
      )}
    </div>
  );
}
