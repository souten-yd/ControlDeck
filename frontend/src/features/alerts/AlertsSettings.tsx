import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog, Skeleton } from "../../components/ui";
import type { ManagedApp } from "../../types";

interface Channel {
  id: number;
  name: string;
  channel_type: string;
  url_preview: string;
  enabled: boolean;
}
interface Rule {
  id: number;
  name: string;
  metric: string;
  metric_label: string;
  operator: string;
  threshold: number;
  duration_seconds: number;
  cooldown_seconds: number;
  app_id: number | null;
  channel_ids: number[];
  enabled: boolean;
}

const METRICS = [
  { value: "cpu_percent", label: "CPU 使用率 (%)" },
  { value: "memory_percent", label: "RAM 使用率 (%)" },
  { value: "cpu_temp_c", label: "CPU 温度 (℃)" },
  { value: "gpu_percent", label: "GPU 使用率 (%)" },
  { value: "gpu_temp_c", label: "GPU 温度 (℃)" },
  { value: "vram_percent", label: "VRAM 使用率 (%)" },
  { value: "disk_percent", label: "ディスク使用率 (%)" },
  { value: "app_down", label: "アプリ停止" },
  { value: "app_health_failed", label: "アプリのヘルスチェック失敗" },
  { value: "app_restart_loop", label: "アプリの再起動回数" },
  { value: "app_log_error", label: "アプリログの ERROR" },
];
const APP_METRICS = new Set(["app_down", "app_health_failed", "app_restart_loop", "app_log_error"]);
const BOOLEAN_METRICS = new Set(["app_down", "app_health_failed", "app_log_error"]);
const OPERATORS = [
  { value: "gt", label: ">" },
  { value: "gte", label: "≥" },
  { value: "lt", label: "<" },
  { value: "lte", label: "≤" },
];

export function AlertsSettings() {
  const canEdit = useAuth((s) => s.can)("settings.manage");
  return (
    <>
      <ChannelsSection canEdit={canEdit} />
      <RulesSection canEdit={canEdit} />
    </>
  );
}

function ChannelsSection({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [adding, setAdding] = useState(false);
  const { data: channels } = useQuery({ queryKey: ["alert-channels"], queryFn: () => api<Channel[]>("/alert-channels") });

  const test = useMutation({
    mutationFn: (id: number) => api<{ ok: boolean }>(`/alert-channels/${id}/test`, { method: "POST" }),
    onSuccess: (r) => show(r.ok ? "テスト通知を送信しました" : "送信に失敗しました", r.ok ? "success" : "error"),
    onError: () => show("送信に失敗しました", "error"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/alert-channels/${id}`, { method: "DELETE" }),
    onSuccess: () => { show("削除しました"); qc.invalidateQueries({ queryKey: ["alert-channels"] }); },
  });

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-500">通知チャンネル</h2>
        {canEdit && (
          <button onClick={() => setAdding(true)} className="rounded-lg bg-accent-50 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400">
            追加
          </button>
        )}
      </div>
      {!channels ? (
        <Skeleton className="h-12" />
      ) : channels.length === 0 ? (
        <p className="text-sm text-zinc-400">メール / Discord / Slack / Webhook を追加すると通知を受け取れます</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {channels.map((c) => (
            <li key={c.id} className="flex items-center gap-3 py-2.5 text-sm">
              <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-zinc-500 dark:bg-zinc-800">{c.channel_type}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate">{c.name}</p>
                <p className="truncate font-mono text-xs text-zinc-400">{c.url_preview}</p>
              </div>
              {canEdit && (
                <>
                  <button onClick={() => test.mutate(c.id)} className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">テスト</button>
                  <button onClick={() => remove.mutate(c.id)} className="text-xs font-medium text-red-600 hover:underline dark:text-red-400">削除</button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {adding && <ChannelForm onClose={() => setAdding(false)} />}
    </section>
  );
}

function ChannelForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [name, setName] = useState("");
  const [type, setType] = useState("discord");
  const [url, setUrl] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpSecurity, setSmtpSecurity] = useState("starttls");
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [fromAddress, setFromAddress] = useState("");
  const [toAddresses, setToAddresses] = useState("");
  const isEmail = type === "email";
  const create = useMutation({
    mutationFn: () => api("/alert-channels", {
      method: "POST",
      json: isEmail ? {
        name,
        channel_type: type,
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_security: smtpSecurity,
        smtp_username: smtpUsername,
        smtp_password: smtpPassword,
        from_address: fromAddress,
        to_addresses: toAddresses.split(",").map((value) => value.trim()).filter(Boolean),
      } : { name, channel_type: type, url },
    }),
    onSuccess: () => { show("追加しました"); qc.invalidateQueries({ queryKey: ["alert-channels"] }); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : "追加に失敗しました", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <BottomSheet title="通知チャンネルを追加" onClose={onClose}>
      <div className="space-y-3">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="名前" className={input} />
        <select value={type} onChange={(e) => setType(e.target.value)} className={input}>
          <option value="discord">Discord</option>
          <option value="slack">Slack</option>
          <option value="webhook">汎用 Webhook</option>
          <option value="email">メール (SMTP)</option>
        </select>
        {isEmail ? (
          <>
            <div className="grid grid-cols-[1fr_7rem] gap-2">
              <label className="text-xs text-zinc-500">SMTP host
                <input value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} placeholder="smtp.example.com" className={`${input} mt-1 font-mono text-xs`} autoCapitalize="none" />
              </label>
              <label className="text-xs text-zinc-500">Port
                <input type="number" min={1} max={65535} value={smtpPort} onChange={(e) => setSmtpPort(Number(e.target.value))} className={`${input} mt-1`} />
              </label>
            </div>
            <label className="block text-xs text-zinc-500">接続保護
              <select value={smtpSecurity} onChange={(e) => setSmtpSecurity(e.target.value)} className={`${input} mt-1`}>
                <option value="starttls">STARTTLS（通常587）</option>
                <option value="tls">TLS（通常465）</option>
                <option value="none">なし（信頼できる内部SMTPのみ）</option>
              </select>
            </label>
            <label className="block text-xs text-zinc-500">SMTP username（任意）
              <input value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)} autoComplete="username" className={`${input} mt-1`} />
            </label>
            <label className="block text-xs text-zinc-500">SMTP password（暗号化保存）
              <input type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)} autoComplete="new-password" className={`${input} mt-1`} />
            </label>
            <label className="block text-xs text-zinc-500">送信元
              <input type="email" value={fromAddress} onChange={(e) => setFromAddress(e.target.value)} placeholder="control-deck@example.com" className={`${input} mt-1`} autoCapitalize="none" />
            </label>
            <label className="block text-xs text-zinc-500">宛先（複数はカンマ区切り、最大20件）
              <input value={toAddresses} onChange={(e) => setToAddresses(e.target.value)} placeholder="admin@example.com" className={`${input} mt-1`} autoCapitalize="none" />
            </label>
          </>
        ) : (
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Webhook URL" className={`${input} font-mono text-xs`} />
        )}
        <button onClick={() => create.mutate()} disabled={!name || (isEmail ? !smtpHost || !fromAddress || !toAddresses : !url) || create.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          追加
        </button>
      </div>
    </BottomSheet>
  );
}

function RulesSection({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const { data: rules } = useQuery({ queryKey: ["alert-rules"], queryFn: () => api<Rule[]>("/alert-rules") });

  const remove = useMutation({
    mutationFn: (id: number) => api(`/alert-rules/${id}`, { method: "DELETE" }),
    onSuccess: () => { show("削除しました"); setDeleting(null); qc.invalidateQueries({ queryKey: ["alert-rules"] }); },
  });

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-500">アラートルール</h2>
        {canEdit && (
          <button onClick={() => setEditing("new")} className="rounded-lg bg-accent-50 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-100 dark:bg-accent-600/15 dark:text-accent-400">
            追加
          </button>
        )}
      </div>
      {!rules ? (
        <Skeleton className="h-12" />
      ) : rules.length === 0 ? (
        <p className="text-sm text-zinc-400">例: CPU 90% が 5 分続いたら Discord 通知</p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {rules.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2.5 text-sm">
              <span className={`h-2 w-2 shrink-0 rounded-full ${r.enabled ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-700"}`} />
              <div className="min-w-0 flex-1">
                <p className="truncate">{r.name}</p>
                <p className="num truncate text-xs text-zinc-400">
                  {r.metric_label} {!BOOLEAN_METRICS.has(r.metric) && `${OPERATORS.find((o) => o.value === r.operator)?.label ?? r.operator} ${r.threshold}`}
                  {r.duration_seconds > 0 && ` · ${r.duration_seconds}秒継続`}
                </p>
              </div>
              {canEdit && (
                <>
                  <button onClick={() => setEditing(r)} className="text-xs font-medium text-accent-600 hover:underline dark:text-accent-400">編集</button>
                  <button onClick={() => setDeleting(r)} className="text-xs font-medium text-red-600 hover:underline dark:text-red-400">削除</button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {editing && <RuleForm rule={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {deleting && (
        <ConfirmDialog
          title={`「${deleting.name}」を削除しますか？`}
          message="このアラートルールを削除します。"
          confirmLabel="削除する"
          onConfirm={() => remove.mutate(deleting.id)}
          onClose={() => setDeleting(null)}
        />
      )}
    </section>
  );
}

function RuleForm({ rule, onClose }: { rule: Rule | null; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data: channels } = useQuery({ queryKey: ["alert-channels"], queryFn: () => api<Channel[]>("/alert-channels") });
  const { data: apps } = useQuery({ queryKey: ["apps"], queryFn: () => api<ManagedApp[]>("/apps") });
  const [form, setForm] = useState({
    name: rule?.name ?? "",
    metric: rule?.metric ?? "cpu_percent",
    operator: rule?.operator ?? "gt",
    threshold: rule?.threshold ?? 90,
    duration_seconds: rule?.duration_seconds ?? 300,
    cooldown_seconds: rule?.cooldown_seconds ?? 600,
    app_id: rule?.app_id ?? null,
    channel_ids: rule?.channel_ids ?? [],
    enabled: rule?.enabled ?? true,
  });
  const save = useMutation({
    mutationFn: () =>
      api(rule ? `/alert-rules/${rule.id}` : "/alert-rules", { method: rule ? "PATCH" : "POST", json: form }),
    onSuccess: () => { show("保存しました"); qc.invalidateQueries({ queryKey: ["alert-rules"] }); onClose(); },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });
  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  const isAppMetric = APP_METRICS.has(form.metric);
  const isBooleanMetric = BOOLEAN_METRICS.has(form.metric);

  return (
    <BottomSheet title={rule ? "ルールを編集" : "アラートルールを追加"} onClose={onClose} wide>
      <div className="space-y-3">
        <input aria-label="ルール名" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="ルール名" className={input} />
        <select
          aria-label="監視条件"
          value={form.metric}
          onChange={(e) => {
            const metric = e.target.value;
            setForm({
              ...form,
              metric,
              app_id: APP_METRICS.has(metric) ? form.app_id : null,
              operator: BOOLEAN_METRICS.has(metric) ? "gte" : form.operator,
              threshold: BOOLEAN_METRICS.has(metric) ? 1 : metric === "app_restart_loop" && form.threshold === 90 ? 3 : form.threshold,
            });
          }}
          className={input}
        >
          {METRICS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        {isAppMetric && (
          <select aria-label="対象アプリ" value={form.app_id ?? ""} onChange={(e) => setForm({ ...form, app_id: e.target.value ? Number(e.target.value) : null })} className={input}>
            <option value="">アプリを選択</option>
            {apps?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
        {!isBooleanMetric && (
          <div className="flex gap-2">
            <select aria-label="比較演算子" value={form.operator} onChange={(e) => setForm({ ...form, operator: e.target.value })} className={`${input} w-24`}>
              {OPERATORS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <input aria-label="しきい値" type="number" min={form.metric === "app_restart_loop" ? 1 : undefined} value={form.threshold} onChange={(e) => setForm({ ...form, threshold: Number(e.target.value) })} placeholder={form.metric === "app_restart_loop" ? "再起動回数" : "しきい値"} className={input} />
          </div>
        )}
        {form.metric !== "app_log_error" && (
          <label className="block text-xs text-zinc-500">
            継続時間（秒）— この時間しきい値を超え続けたら通知
            <input aria-label="継続時間" type="number" value={form.duration_seconds} onChange={(e) => setForm({ ...form, duration_seconds: Number(e.target.value) })} className={`${input} mt-1`} />
          </label>
        )}
        <div>
          <p className="mb-1 text-xs text-zinc-500">通知先チャンネル</p>
          {channels && channels.length > 0 ? (
            <div className="space-y-1">
              {channels.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.channel_ids.includes(c.id)}
                    onChange={(e) =>
                      setForm({ ...form, channel_ids: e.target.checked ? [...form.channel_ids, c.id] : form.channel_ids.filter((x) => x !== c.id) })
                    }
                    className="h-4 w-4 accent-current"
                  />
                  {c.name}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-xs text-zinc-400">先に通知チャンネルを追加してください</p>
          )}
        </div>
        <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-3.5 py-2.5 dark:border-zinc-700">
          <span className="text-sm">有効</span>
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} className="h-5 w-5 accent-current" />
        </label>
        <button onClick={() => save.mutate()} disabled={!form.name || (isAppMetric && form.app_id == null) || save.isPending} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          保存
        </button>
      </div>
    </BottomSheet>
  );
}
