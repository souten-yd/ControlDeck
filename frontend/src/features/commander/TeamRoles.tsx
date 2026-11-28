/** Nexarch の AI チーム T3b: 担い手の選び手（コンソールとモデルを別々に）・役割・チームリーダーの任命・引き継ぎ先を選ぶ・
 * 目標からチームを作る・チームの既定（既定のチームリーダーと保存先）。
 *
 * UI の決まり（2026-11-27 利用者）: 不要な表示は隠し、必要ならエキスパートで開く。エキスパートが切のときは
 * モデル・考えるレベル・役割の詳細（数・プライマリ／セカンダリ）・チームリーダーの任命を出さない（既定の値で動く）。
 * 毎回使う「目標」「引き継ぎ先を選ぶ」と、危険・要対応（引き継ぎ先なし・上限）は切でも出す。 */
import { useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  chooseHandoff, createAutoTeam, emptyEndpoint, fetchAccounts, fetchCommanderSettings, fetchModelChoices, fetchModels, putTeamDefaults, setTeamRoles,
  setTeamLeader, MODEL_GROUP_LABEL, MODEL_GROUP_ORDER, type Endpoint, type ModelChoice, type Team, type TeamRoleDef, type TeamTask,
} from "../../api/commander";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { useToasts } from "../../stores";
import { MaterialAttach } from "./TeamMaterials";
import { WORKDIR_SOURCE } from "./NewConsoleSheet";

const inputClass = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const textareaClass = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const fieldLabel = "mb-1 mt-3 block text-xs font-medium text-zinc-600 dark:text-zinc-300";
const groupClass = "mt-3 min-w-0 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700";
const groupTitle = "text-xs font-semibold text-zinc-600 dark:text-zinc-300";
const CLI_OPTIONS: ReadonlyArray<readonly [string, string]> = [["claude", "Claude Code"], ["codex", "Codex"], ["opencode", "OpenCode"]];
const CLI_NAME: Record<string, string> = Object.fromEntries(CLI_OPTIONS);

// ---------------------------------------------------------------- エキスパートの切り替え（この端末の利用者ごとに覚える・既定は切）

const EXPERT_KEY = "control-deck:commander-expert";
const expertListeners = new Set<() => void>();
let expertMemory = false;
function readExpert(): boolean {
  try { return localStorage.getItem(EXPERT_KEY) === "1"; } catch { return expertMemory; }
}
function subscribeExpert(listener: () => void) {
  expertListeners.add(listener);
  return () => { expertListeners.delete(listener); };
}
export function setExpert(value: boolean) {
  expertMemory = value;
  try { localStorage.setItem(EXPERT_KEY, value ? "1" : "0"); } catch { /* 保存できない端末はこの画面の間だけ */ }
  expertListeners.forEach((l) => l());
}
export function useExpert(): boolean {
  return useSyncExternalStore(subscribeExpert, readExpert, () => false);
}

export function ExpertSwitch() {
  const on = useExpert();
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label="エキスパート" data-commander-expert={on ? "on" : "off"}
      title="細かい設定と詳しい表示を開きます（この端末に覚えます）" onClick={() => setExpert(!on)}
      className="flex min-h-11 shrink-0 items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300"
    >
      <span className={`relative h-5 w-9 rounded-full transition ${on ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
      </span>
      エキスパート
    </button>
  );
}

// ---------------------------------------------------------------- 表示

export function endpointText(ep: Endpoint | null | undefined): string {
  if (!ep) return "未設定";
  const parts = [`${CLI_NAME[ep.cli] ?? ep.cli}${ep.account && ep.account !== "default" ? `（${ep.account}）` : ""}`];
  if (ep.model) parts.push(ep.model);
  if (ep.effort) parts.push(ep.effort);
  return parts.join("・") + (ep.kind === "console" ? "・画面あり" : "");
}

function fmtClock(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ---------------------------------------------------------------- 担い手の選び手: 使えるモデルの一覧から 1 つ選ぶ（細かい指定はエキスパート）

const sameChoice = (choice: ModelChoice, ep: Endpoint) => {
  const c = choice.endpoint;
  return c.cli === ep.cli && (c.account || "default") === (ep.account || "default")
    && (c.model || "") === (ep.model || "") && (c.effort || "") === (ep.effort || "");
};

/** 使えるモデルをまとまり（ローカル・OpenCode・OpenRouter・Claude・Codex）ごとに並べた 1 つの選択。 */
function ModelChoiceSelect({
  value, onChange, choices, note, loading, id,
}: {
  value: Endpoint; onChange: (value: Endpoint) => void; choices: ModelChoice[]; note?: string;
  loading: boolean; id: string;
}) {
  const selected = choices.find((c) => sameChoice(c, value));
  const groups = new Map<string, ModelChoice[]>();
  for (const c of choices) {
    const key = c.group || "other";
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  const order = [...MODEL_GROUP_ORDER.filter((g) => groups.has(g)), ...[...groups.keys()].filter((g) => !MODEL_GROUP_ORDER.includes(g))];
  return (
    <>
      <label className={fieldLabel} htmlFor={id}>モデル（使えるものから選ぶ）</label>
      <select
        id={id} data-endpoint-choice value={selected?.id ?? ""}
        disabled={loading}
        onChange={(e) => {
          const next = choices.find((c) => c.id === e.target.value);
          if (next) onChange({ ...next.endpoint, kind: value.kind });
        }}
        className={inputClass}
      >
        <option value="" disabled>{loading ? "読み込み中…" : selected ? "いまの設定" : choices.length ? "選んでください" : "選べるモデルがありません"}</option>
        {order.map((g) => (
          <optgroup key={g} label={MODEL_GROUP_LABEL[g] ?? g}>
            {(groups.get(g) ?? []).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </optgroup>
        ))}
      </select>
      <p className="mt-1 break-words text-[11px] text-zinc-500 dark:text-zinc-400">{note || (loading ? "" : "選ぶと CLI・接続・モデル・考えるレベルがまとめて決まります。")}</p>
      {!selected && value.cli && (
        <p data-endpoint-current className="mt-1 break-words text-[11px] text-zinc-500 dark:text-zinc-400">いまの設定: {endpointText(value)}</p>
      )}
    </>
  );
}

export function EndpointPicker({
  value, onChange, expert, withKind = true, idPrefix = "ep",
}: {
  value: Endpoint; onChange: (value: Endpoint) => void; expert: boolean; withKind?: boolean; idPrefix?: string;
}) {
  const accounts = useQuery({
    queryKey: ["commander-accounts", value.cli], queryFn: () => fetchAccounts(value.cli), enabled: expert,
  });
  const models = useQuery({
    queryKey: ["commander-models", value.cli, value.account], queryFn: () => fetchModels(value.cli, value.account),
    staleTime: 5 * 60_000, enabled: expert,
  });
  const choices = useQuery({ queryKey: ["commander-model-choices"], queryFn: fetchModelChoices, staleTime: 5 * 60_000 });
  const info = models.data;
  const chosen = info?.models.find((m) => m.id === value.model);
  const efforts = chosen && chosen.efforts.length ? chosen.efforts : info?.efforts ?? [];
  const set = (patch: Partial<Endpoint>) => onChange({ ...value, ...patch });
  const choiceList = choices.data?.choices ?? [];
  return (
    <div data-endpoint-picker>
      <fieldset data-endpoint-model className={groupClass}>
        <legend className={`px-1 ${groupTitle}`}>モデル</legend>
        <ModelChoiceSelect
          id={`${idPrefix}-choice`} value={value} onChange={onChange}
          choices={choiceList} note={choices.data?.note} loading={choices.isLoading}
        />
        {expert && (
          <>
            <label className={fieldLabel} htmlFor={`${idPrefix}-model`}>モデルを直接指定（一覧に無いもの）</label>
            <input
              id={`${idPrefix}-model`} list={`${idPrefix}-model-list`} value={value.model} maxLength={80} autoComplete="off"
              placeholder="空なら CLI の既定" onChange={(e) => set({ model: e.target.value.trim(), effort: "" })} className={inputClass}
            />
            <datalist id={`${idPrefix}-model-list`}>
              {(info?.models ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </datalist>
            {info?.note && <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">{info.note}</p>}
            <label className={fieldLabel} htmlFor={`${idPrefix}-effort`}>考えるレベル</label>
            {efforts.length > 0 ? (
              <select id={`${idPrefix}-effort`} value={value.effort} onChange={(e) => set({ effort: e.target.value })} className={inputClass}>
                <option value="">既定</option>
                {efforts.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            ) : (
              <input
                id={`${idPrefix}-effort`} value={value.effort} maxLength={20} placeholder="空なら既定（variant があるモデルだけ）"
                onChange={(e) => set({ effort: e.target.value.trim() })} className={inputClass}
              />
            )}
          </>
        )}
      </fieldset>
      {expert && (
        <fieldset data-endpoint-console className={groupClass}>
          <legend className={`px-1 ${groupTitle}`}>コンソール・接続（細かい指定）</legend>
          {withKind && (
            <>
              <label className={fieldLabel} htmlFor={`${idPrefix}-kind`}>種類</label>
              <select id={`${idPrefix}-kind`} value={value.kind} onChange={(e) => set({ kind: e.target.value as Endpoint["kind"] })} className={inputClass}>
                <option value="headless">ヘッドレス（画面なし）</option>
                <option value="console">画面あり（Nexarch のコンソールを開く）</option>
              </select>
            </>
          )}
          <label className={fieldLabel} htmlFor={`${idPrefix}-cli`}>CLI</label>
          <select
            id={`${idPrefix}-cli`} value={value.cli}
            onChange={(e) => set({ cli: e.target.value, account: "default", model: "", effort: "" })} className={inputClass}
          >
            {CLI_OPTIONS.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          <label className={fieldLabel} htmlFor={`${idPrefix}-account`}>アカウント・接続</label>
          <select id={`${idPrefix}-account`} value={value.account} onChange={(e) => set({ account: e.target.value, model: "", effort: "" })} className={inputClass}>
            {(accounts.data ?? [{ name: "default", label: "", title: "", default: true, logged_in: null }]).map((a) => (
              <option key={a.name} value={a.name}>{a.label || a.title || (a.default ? "既定" : a.name)}</option>
            ))}
          </select>
        </fieldset>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 役割

export function RolesPanel({ team, expert, onEdit }: { team: Team; expert: boolean; onEdit: () => void }) {
  const roles = team.roles ?? [];
  if (roles.length === 0) return null;
  if (!expert) {
    return (
      <p data-team-roles-summary className="mb-3 min-w-0 break-words text-xs text-zinc-600 dark:text-zinc-300">
        <span className="font-semibold">役割</span> {roles.map((r) => r.name).join("・")}
      </p>
    );
  }
  const autos = (id: string) => team.members.filter((m) => m.auto && m.role_id === id).length;
  return (
    <section data-team-roles aria-label="役割" className="mb-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">役割・自動の振り分け</h3>
        <button type="button" onClick={onEdit} className="min-h-9 text-xs font-semibold text-accent-600 underline dark:text-accent-400">役割を編集</button>
      </div>
      <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {roles.map((r) => {
          const secondary = r.secondary ?? [];
          let state = "担い手が未設定（メンバーの中から選びます）";
          if (r.primary) {
            if (r.active_index == null) state = "すべて回復待ち";
            else if (r.on_secondary) {
              state = `セカンダリで動作中${r.primary_recovers_at ? `・プライマリは ${fmtClock(r.primary_recovers_at)} に回復` : ""}`;
            } else state = "プライマリで動作中";
          }
          return (
            <li key={r.id} data-team-role={r.name} className="min-w-0 rounded-2xl border border-zinc-200 bg-white p-2.5 text-xs dark:border-zinc-800 dark:bg-zinc-900">
              <p className="flex min-w-0 items-baseline gap-2">
                <strong className="min-w-0 truncate text-sm">{r.name}</strong>
                <span className="num shrink-0 text-zinc-500">{r.running ?? 0}／{r.count}</span>
                {autos(r.id) > 0 && <span className="shrink-0 text-zinc-400">枠 {autos(r.id)}</span>}
              </p>
              <p className="mt-1 break-words text-zinc-600 dark:text-zinc-300">プライマリ: {endpointText(r.primary)}</p>
              <p className="break-words text-zinc-600 dark:text-zinc-300">
                セカンダリ: {secondary.length ? secondary.map(endpointText).join(" → ") : "なし"}
              </p>
              <p data-team-role-state className={`mt-1 break-words font-semibold ${r.on_secondary || r.active_index == null && r.primary ? "text-amber-700 dark:text-amber-400" : "text-zinc-500"}`}>{state}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 役割の編集（チームの役割・既定の役割で共通）。名前・数・説明・プライマリ・セカンダリ。 */
export function RolesEditor({ roles, onChange, expert }: { roles: TeamRoleDef[]; onChange: (roles: TeamRoleDef[]) => void; expert: boolean }) {
  const [picking, setPicking] = useState<{ index: number; slot: "primary" | "secondary" } | null>(null);
  const [draft, setDraft] = useState<Endpoint>(emptyEndpoint());
  const patch = (index: number, fields: Partial<TeamRoleDef>) => onChange(roles.map((r, i) => (i === index ? { ...r, ...fields } : r)));
  const commit = () => {
    if (!picking) return;
    const role = roles[picking.index];
    if (picking.slot === "primary") patch(picking.index, { primary: draft });
    else patch(picking.index, { secondary: [...role.secondary, draft] });
    setPicking(null);
  };
  return (
    <ul data-roles-editor className="mt-3 grid gap-3">
      {roles.map((r, i) => (
        <li key={r.id || `new-${i}`} data-role-edit={r.name} className="min-w-0 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
          <label className={fieldLabel} htmlFor={`role-name-${i}`}>役割の名前</label>
          <input id={`role-name-${i}`} value={r.name} maxLength={20} onChange={(e) => patch(i, { name: e.target.value })} className={inputClass} />
          <label className={fieldLabel} htmlFor={`role-count-${i}`}>同時に呼ぶ最大数</label>
          <select id={`role-count-${i}`} value={r.count} onChange={(e) => patch(i, { count: Number(e.target.value) })} className={inputClass}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <label className={fieldLabel} htmlFor={`role-note-${i}`}>説明（チームリーダーに渡します）</label>
          <input id={`role-note-${i}`} value={r.note} maxLength={200} onChange={(e) => patch(i, { note: e.target.value })} className={inputClass} />
          <p className={`${fieldLabel} mt-3`}>プライマリ</p>
          <p className="flex min-w-0 items-center gap-2 text-sm">
            <span className="min-w-0 flex-1 break-words">{endpointText(r.primary)}</span>
            <button type="button" onClick={() => { setDraft(r.primary ?? emptyEndpoint()); setPicking({ index: i, slot: "primary" }); }} className={`${secondaryButtonClass} shrink-0`}>
              {r.primary ? "変える" : "選ぶ"}
            </button>
            {r.primary && <button type="button" onClick={() => patch(i, { primary: null })} className={`${secondaryButtonClass} shrink-0`}>外す</button>}
          </p>
          <p className={`${fieldLabel} mt-3`}>セカンダリ（上から順に引き継ぐ・3 つまで）</p>
          <ol className="grid gap-1">
            {r.secondary.map((ep, j) => (
              <li key={j} className="flex min-w-0 items-center gap-2 text-sm">
                <span className="num w-4 shrink-0 text-xs text-zinc-400">{j + 1}</span>
                <span className="min-w-0 flex-1 break-words">{endpointText(ep)}</span>
                <button type="button" aria-label={`セカンダリ ${j + 1} を外す`} onClick={() => patch(i, { secondary: r.secondary.filter((_, k) => k !== j) })} className={`${secondaryButtonClass} shrink-0`}>外す</button>
              </li>
            ))}
          </ol>
          {r.secondary.length < 3 && (
            <button type="button" onClick={() => { setDraft(emptyEndpoint()); setPicking({ index: i, slot: "secondary" }); }} className={`mt-1 ${secondaryButtonClass}`}>セカンダリを足す</button>
          )}
          {picking?.index === i && (
            <div data-role-endpoint-picker className="mt-2 rounded-xl bg-zinc-50 p-2 dark:bg-zinc-800/50">
              <EndpointPicker value={draft} onChange={setDraft} expert={expert} idPrefix={`role-${i}`} />
              <div className="mt-2 flex gap-2">
                <button type="button" onClick={commit} className={`flex-1 ${primaryButtonClass}`}>{picking.slot === "primary" ? "プライマリにする" : "セカンダリに足す"}</button>
                <button type="button" onClick={() => setPicking(null)} className={secondaryButtonClass}>やめる</button>
              </div>
            </div>
          )}
          {roles.length > 1 && (
            <button type="button" onClick={() => onChange(roles.filter((_, k) => k !== i))} className={`mt-3 ${secondaryButtonClass} text-red-600 dark:text-red-400`}>この役割を削除</button>
          )}
        </li>
      ))}
      {roles.length < 8 && (
        <li>
          <button
            type="button" className={secondaryButtonClass}
            onClick={() => onChange([...roles, { id: "", name: "新しい役割", count: 1, note: "", primary: null, secondary: [] }])}
          >役割を足す</button>
        </li>
      )}
    </ul>
  );
}

export function RolesSheet({ team, expert, onClose, onSaved }: { team: Team; expert: boolean; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [roles, setRoles] = useState<TeamRoleDef[]>(() => (team.roles ?? []).map((r) => ({ id: r.id, name: r.name, count: r.count, note: r.note, primary: r.primary, secondary: r.secondary ?? [] })));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await setTeamRoles(team.id, roles);
      onSaved();
      show("役割を保存しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="役割を編集" onClose={onClose} wide>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        タスクには役割が付きます。自動の振り分け: 配り手は役割の数まで担い手を呼び出し、上限で止まればセカンダリへ移り、回復したら次のタスクからプライマリへ戻します。
      </p>
      <RolesEditor roles={roles} onChange={setRoles} expert={expert} />
      <button type="button" disabled={busy || roles.some((r) => !r.name.trim())} onClick={() => void save()} className={`mt-4 w-full ${primaryButtonClass}`}>保存</button>
    </BottomSheet>
  );
}

// ---------------------------------------------------------------- チームの保存先（一度だけ）

export function TeamRootBlock({ expert }: { expert: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const [picking, setPicking] = useState(false);
  const root = data?.team_root ?? "";
  const choose = async (path: string) => {
    setPicking(false);
    try {
      await putTeamDefaults({ team_root: path });
      qc.invalidateQueries({ queryKey: ["commander-settings"] });
      show("チームの保存先を保存しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    }
  };
  if (!data) return null;
  return (
    <section data-team-root className={groupClass}>
      <h4 className={groupTitle}>チームの保存先</h4>
      {root ? (
        <p className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs">
          <span data-team-root-path className="min-w-0 break-all font-mono">{root}</span>
          {expert && <button type="button" onClick={() => setPicking(true)} className="font-semibold text-accent-600 underline dark:text-accent-400">変える</button>}
        </p>
      ) : (
        <>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
            未設定です。一度だけ選ぶと、チームを作るたびにその下へ「名前-日時」のフォルダを自動で作ります（未設定のままなら CodeDEV に作ります）。
          </p>
          <button type="button" onClick={() => setPicking(true)} className={`mt-2 ${secondaryButtonClass}`}>保存先を選ぶ</button>
        </>
      )}
      {picking && (
        <FilePicker mode="dir" title="チームの保存先を選択" initialPath={root || undefined} source={WORKDIR_SOURCE}
          onSelect={(path) => void choose(path)} onClose={() => setPicking(false)} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------- チームリーダーの任命（チーム）・チームの既定（Nexarch の設定）

export function LeaderSheet({ team, expert, onClose, onSaved }: { team: Team; expert: boolean; onClose: () => void; onSaved: () => void }) {
  const show = useToasts((s) => s.show);
  const [value, setValue] = useState<Endpoint>(team.leader ?? team.leader_effective ?? emptyEndpoint());
  const [busy, setBusy] = useState(false);
  const save = async (leader: Endpoint | null) => {
    setBusy(true);
    try {
      await setTeamLeader(team.id, leader ? { ...leader, kind: "headless" } : null);
      onSaved();
      show(leader ? "チームリーダーを任命しました" : "既定のチームリーダーに戻しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="チームリーダーを任命" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        目標を計画してタスクを配る AI です。一文で頼まれると、画面なし（ヘッドレス）で長く動き、MCP の道具で調べ・計画し・段ごとに担い手へ振ります。任命しなければ既定のチームリーダーを使います（今: {endpointText(team.leader_effective)}）。
      </p>
      <EndpointPicker value={value} onChange={setValue} expert={expert} withKind={false} idPrefix="leader" />
      <TeamRootBlock expert={expert} />
      <button type="button" disabled={busy} onClick={() => void save(value)} className={`mt-4 w-full ${primaryButtonClass}`}>任命する</button>
      {team.leader && <button type="button" disabled={busy} onClick={() => void save(null)} className={`mt-2 w-full ${secondaryButtonClass}`}>既定のチームリーダーに戻す</button>}
    </BottomSheet>
  );
}

export function TeamDefaultsSheet({ expert, onClose }: { expert: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const [leader, setLeader] = useState<Endpoint | null>(null);
  const [roles, setRoles] = useState<TeamRoleDef[] | null>(null);
  const [busy, setBusy] = useState(false);
  const shownLeader = leader ?? data?.default_leader ?? emptyEndpoint();
  const shownRoles = roles ?? data?.default_roles ?? [];
  const save = async (body: Parameters<typeof putTeamDefaults>[0], done: string) => {
    setBusy(true);
    try {
      await putTeamDefaults(body);
      qc.invalidateQueries({ queryKey: ["commander-settings"] });
      show(done);
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    }
    setBusy(false);
  };
  return (
    <BottomSheet title="チームの既定" onClose={onClose} wide>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        新しいチームはここから始まります。既定のチームリーダーは、チームごとに任命し直せます。
      </p>
      <h4 className={`${groupTitle} mt-3`}>既定のチームリーダー{data?.default_leader ? `（今: ${endpointText(data.default_leader)}）` : "（未設定: 導入済みの CLI を使います）"}</h4>
      <EndpointPicker value={shownLeader} onChange={setLeader} expert={expert} withKind={false} idPrefix="default-leader" />
      <div className="mt-2 flex gap-2">
        <button type="button" disabled={busy} onClick={() => void save({ default_leader: { ...shownLeader, kind: "headless" } }, "既定のチームリーダーを保存しました")} className={`flex-1 ${primaryButtonClass}`}>既定のチームリーダーにする</button>
        {data?.default_leader && (
          <button type="button" disabled={busy} onClick={() => void save({ default_leader: null }, "既定のチームリーダーを外しました")} className={secondaryButtonClass}>外す</button>
        )}
      </div>
      <TeamRootBlock expert={expert} />
      {expert && (
        <section data-default-roles className="mt-4 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          <h4 className={groupTitle}>既定の役割{shownRoles.length === 0 ? "（未設定: 実装・レビュー・試験）" : ""}</h4>
          <RolesEditor
            roles={shownRoles.length ? shownRoles : [{ id: "", name: "実装", count: 2, note: "", primary: null, secondary: [] }]}
            onChange={setRoles} expert={expert}
          />
          <button type="button" disabled={busy || !roles} onClick={() => void save({ default_roles: roles ?? [] }, "既定の役割を保存しました")} className={`mt-3 w-full ${secondaryButtonClass}`}>既定の役割を保存</button>
          {shownRoles.length > 0 && (
            <button type="button" disabled={busy} onClick={() => { setRoles(null); void save({ default_roles: [] }, "既定の役割を組み込みに戻しました"); }} className={`mt-2 w-full ${secondaryButtonClass}`}>組み込みの役割に戻す</button>
          )}
        </section>
      )}
    </BottomSheet>
  );
}

// ---------------------------------------------------------------- 引き継ぎ先を選ぶ

export function HandoffSheet({
  teamId, task, roleName, expert, onClose, onDone,
}: { teamId: string; task: Pick<TeamTask, "id" | "title" | "role">; roleName?: string; expert: boolean; onClose: () => void; onDone: () => void }) {
  const show = useToasts((s) => s.show);
  const [value, setValue] = useState<Endpoint>(emptyEndpoint());
  const [add, setAdd] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await chooseHandoff(teamId, task.id, value, add && Boolean(task.role));
      onDone();
      show("引き継ぎ先を選んで再開しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "引き継げませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="引き継ぎ先を選ぶ" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        #{task.id}「{task.title}」は上限で止まり、引き継ぎ先がありません。担い手を選ぶと、ここまでの作業を渡してすぐ再開します。
      </p>
      <EndpointPicker value={value} onChange={setValue} expert={expert} idPrefix="handoff" />
      {task.role && (
        <label className="mt-3 flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={add} onChange={(e) => setAdd(e.target.checked)} />
          {roleName ? `「${roleName}」のセカンダリにも追加する` : "この役割のセカンダリにも追加する"}
        </label>
      )}
      <button type="button" disabled={busy} onClick={() => void submit()} className={`mt-4 w-full ${primaryButtonClass}`}>引き継いで再開</button>
    </BottomSheet>
  );
}

// ---------------------------------------------------------------- 目標からチームを作る

export function AutoTeamSheet({ expert, onClose, onCreated }: { expert: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const show = useToasts((s) => s.show);
  const [goal, setGoal] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const submit = async () => {
    setBusy(true);
    try {
      const team = await createAutoTeam(goal.trim(), workdir, "", paths);
      onCreated(team.id);
      show("チームリーダーに頼みました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "作れませんでした", "error");
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="作業場所を選んで頼む" onClose={onClose}>
      <label className={fieldLabel} htmlFor="auto-goal">目標</label>
      <textarea id="auto-goal" rows={5} value={goal} maxLength={20000} onChange={(e) => setGoal(e.target.value)} className={textareaClass}
        placeholder="例: ログイン機能を作って、試験まで通す" />
      <p className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
        チームリーダー（{endpointText(data?.default_leader)}）が調べ・計画し、段ごとに担い手へ振って、統合・試験まで進めます。
      </p>
      {data && !data.team_root && <TeamRootBlock expert={expert} />}
      <MaterialAttach paths={paths} onChange={setPaths} />
      {expert && (
        <div className={groupClass}>
          <p className={groupTitle}>作業場所</p>
          <p className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs">
            <span className="min-w-0 break-all font-mono">{workdir || "新しい作業場所（自動）"}</span>
            <button type="button" onClick={() => setPicking(true)} className="font-semibold text-accent-600 underline dark:text-accent-400">フォルダを選ぶ</button>
            {workdir && <button type="button" onClick={() => setWorkdir("")} className="font-semibold text-accent-600 underline dark:text-accent-400">自動に戻す</button>}
          </p>
        </div>
      )}
      <button type="button" disabled={!goal.trim() || busy} onClick={() => void submit()} className={`mt-4 w-full ${primaryButtonClass}`}>チームを作る</button>
      {picking && (
        <FilePicker mode="dir" title="作業場所を選択" initialPath={workdir || undefined} source={WORKDIR_SOURCE}
          onSelect={(path) => { setWorkdir(path); setPicking(false); }} onClose={() => setPicking(false)} />
      )}
    </BottomSheet>
  );
}
