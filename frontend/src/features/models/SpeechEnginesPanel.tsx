/** Models の「TTS / ASR」タブ。音声認識と音声合成のエンジンを並べ、使うものを選び、導入し、試し聞きする。
 *
 * 2026-11-19 利用者「TTS と ASR は同じ項目に。ASR は Whisper ともう一つ（Haas-ja）、TTS はまず Supertonic 3。
 * 各 TTS / ASR の利用するエンジンを選択できるように」。 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getSpeechEngines, installSpeechEngine, uninstallSpeechEngine, saveSpeechSettings, selectSpeechEngine, synthesizeSpeech,
  type InputFilter, type SpeechEngine, type SpeechEngines, type SpeechKind, type SpeechSettings } from "../../api/speech";
import { Toggle } from "./formControls";
import { useAuth, useToasts } from "../../stores";
import { ConfirmDialog, Skeleton } from "../../components/ui";

const KIND_LABEL: Record<SpeechKind, string> = { asr: "ASR（音声認識）", tts: "TTS（音声合成）" };
const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(2)} GB`;

export function SpeechEnginesPanel() {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["speech-engines"],
    queryFn: getSpeechEngines,
    // 導入中だけ速く見る
    refetchInterval: (query) => (query.state.data?.engines.some((e) => e.installing) ? 2000 : 15000),
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [removing, setRemoving] = useState<SpeechEngine | null>(null);
  const manage = can("settings.manage");

  if (!data) return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;

  const select = async (kind: SpeechKind, engine: string) => {
    try {
      const next = await selectSpeechEngine(kind, engine);
      qc.setQueryData(["speech-engines"], next);
      show(`${KIND_LABEL[kind]} を ${data.engines.find((e) => e.id === engine)?.name ?? engine} にしました`);
    } catch (e) { show(e instanceof Error ? e.message : "切り替えに失敗しました", "error"); }
  };
  const save = async (values: Parameters<typeof saveSpeechSettings>[0], message = "保存しました") => {
    try {
      qc.setQueryData(["speech-engines"], await saveSpeechSettings(values));
      show(message);
    } catch (e) { show(e instanceof Error ? e.message : "保存できませんでした", "error"); }
  };
  const install = async (engine: SpeechEngine) => {
    setBusy(engine.id);
    try {
      await installSpeechEngine(engine.id);
      show(`${engine.name} の導入を開始しました（サーバー側で継続）`, "info");
      void qc.invalidateQueries({ queryKey: ["speech-engines"] });
      void qc.invalidateQueries({ queryKey: ["features"] });
    } catch (e) { show(e instanceof Error ? e.message : "導入を開始できませんでした", "error"); }
    finally { setBusy(null); }
  };

  const uninstall = async (engine: SpeechEngine) => {
    try {
      qc.setQueryData(["speech-engines"], await uninstallSpeechEngine(engine.id));
      show(`${engine.name} をアンインストールしました`);
      void qc.invalidateQueries({ queryKey: ["features"] });
    } catch (e) { show(e instanceof Error ? e.message : "アンインストールできませんでした", "error"); }
  };

  return (
    <div className="space-y-4">
      {removing && (
        <ConfirmDialog title={`${removing.name} をアンインストールしますか？`}
          message={removing.archive ? "実行環境を消します。保管庫（ソース・重み）は残るので、入れ直すときは取り直しません。"
            : "このエンジンのモデルを消します。もう一度使うときは導入し直します。"}
          confirmLabel="アンインストール"
          onConfirm={() => { void uninstall(removing); setRemoving(null); }} onClose={() => setRemoving(null)} />
      )}
      <p className="px-0.5 text-xs leading-relaxed text-zinc-500">
        AI チャットの音声入力（ASR）と読み上げ（TTS）に使うエンジンです。どちらも CPU で動き、GPU は使いません。
      </p>
      {(["asr", "tts"] as const).map((kind) => {
        const engines = data.engines.filter((e) => e.kind === kind);
        const chosen = data.selected[kind];
        const active = kind === "asr" ? data.active_asr : (engines.find((e) => e.id === chosen && e.ready)?.id ?? null);
        return (
          <section key={kind} className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">{KIND_LABEL[kind]}</h2>
              <label className="ml-auto flex min-w-0 items-center gap-2 text-xs text-zinc-500">
                <span className="whitespace-nowrap">使うエンジン</span>
                <select value={chosen} disabled={!manage} aria-label={`${KIND_LABEL[kind]}で使うエンジン`}
                  onChange={(event) => void select(kind, event.target.value)}
                  className="min-h-10 min-w-0 max-w-[12rem] rounded-xl border border-zinc-300 bg-white px-2 text-sm text-zinc-900 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
                  {engines.map((e) => <option key={e.id} value={e.id}>{e.name}{e.ready ? "" : "（未導入）"}</option>)}
                </select>
              </label>
            </div>
            {kind === "asr" && !active && engines.some((e) => e.ready) && (
              <p className="mb-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                選んだエンジンが未導入です。言語の違うエンジンでは代わりに聞きません。導入してください。
              </p>
            )}
            {active && active !== chosen && (
              <p className="mb-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                選んだエンジンが未導入のため、{engines.find((e) => e.id === active)?.name} で動いています。
              </p>
            )}
            <div className="space-y-2">
              {engines.map((engine) => (
                <EngineRow key={engine.id} engine={engine} chosen={engine.id === chosen} canInstall={manage}
                  starting={busy === engine.id} onInstall={() => void install(engine)} onRemove={() => setRemoving(engine)} />
              ))}
            </div>
            {/* 選んだエンジンに対応する設定だけを出す（2026-11-19 利用者の依頼） */}
            {kind === "asr"
              ? <AsrSettings chosen={chosen} data={data} canEdit={manage} save={save} />
              : <TtsSettings engine={engines.find((e) => e.id === chosen)} settings={data.selected} canEdit={manage} save={save} />}
          </section>
        );
      })}
    </div>
  );
}

function EngineRow({ engine, chosen, canInstall, starting, onInstall, onRemove }: {
  engine: SpeechEngine; chosen: boolean; canInstall: boolean; starting: boolean; onInstall: () => void; onRemove: () => void;
}) {
  const archive = engine.archive;
  return (
    <div className={`rounded-xl border p-3 ${chosen ? "border-accent-400 dark:border-accent-700" : "border-zinc-200 dark:border-zinc-700"}`}>
      <div className="flex items-center gap-3">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${engine.ready ? "bg-emerald-500" : "bg-zinc-200 dark:bg-zinc-700"}`}
          title={engine.ready ? "導入済み" : "未導入"} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-2 text-sm font-semibold">
            <span className="min-w-0 break-words">{engine.name}</span>
            {chosen && <span className="rounded bg-accent-50 px-1.5 py-0.5 text-[10px] font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-300">使用中</span>}
          </p>
          <p className="text-xs text-zinc-500">{engine.summary}</p>
        </div>
        {!engine.ready && canInstall && (
          <button type="button" onClick={onInstall} disabled={engine.installing || starting}
            className="min-h-10 shrink-0 rounded-xl bg-accent-600 px-3.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            {engine.installing || starting ? "導入中…" : "導入"}
          </button>
        )}
        {engine.ready && canInstall && (
          <button type="button" onClick={onRemove}
            className="min-h-10 shrink-0 rounded-xl px-3 text-xs font-medium text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">
            アンインストール
          </button>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-zinc-500">
        <dt>実行</dt><dd className="min-w-0 break-words">{engine.runtime}</dd>
        <dt>モデル</dt><dd className="min-w-0 break-all">{engine.model} <span className="num">· {gb(engine.model_bytes)}</span></dd>
        {engine.license && (<><dt>ライセンス</dt><dd className="min-w-0 break-words">{engine.license}</dd></>)}
        {archive && (
          <>
            <dt>保管庫</dt>
            <dd className="min-w-0 break-all">
              <span className={archive.complete ? "text-emerald-600 dark:text-emerald-400" : undefined}>
                {archive.complete ? "揃っています" : `重み ${archive.model_files}/${archive.model_files_total}・ソース ${Object.values(archive.git).filter(Boolean).length}/${Object.keys(archive.git).length}`}
              </span>
              {" "}— {archive.path}
            </dd>
          </>
        )}
      </dl>
      {engine.installing && <p className="mt-2 animate-pulse text-[11px] text-zinc-400">導入中 — サーバー側で実行中。この画面を閉じても継続します</p>}
      {engine.error && <p className="mt-2 break-words text-[11px] text-amber-700 dark:text-amber-300">{engine.error}</p>}
      {archive?.error && <p className="mt-1 break-words text-[11px] text-amber-700 dark:text-amber-300">{archive.error}</p>}
    </div>
  );
}

type Save = (values: Parameters<typeof saveSpeechSettings>[0], message?: string) => Promise<void>;
const fieldClass = "min-h-10 min-w-0 rounded-xl border border-zinc-300 bg-white px-2 text-sm text-zinc-900 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";
const boxClass = "mt-3 space-y-3 border-t border-zinc-100 pt-3 dark:border-zinc-800";

/** 数の入力。打っている途中では送らず、離れたとき・Enter で範囲に収めて保存する */
function NumberField({ label, hint, value, min, max, step, disabled, onCommit, suffix }: {
  label: string; hint?: string; value: number; min: number; max: number; step: number; disabled?: boolean;
  onCommit: (value: number) => void; suffix?: string;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number(text);
    const next = Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : value;
    setText(String(next));
    if (next !== value) onCommit(next);
  };
  return (
    <label className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
      <span className="min-w-0 flex-1">{label}{hint && <span className="block text-[11px] text-zinc-400">{hint}</span>}</span>
      <span className="flex shrink-0 items-center gap-1">
        <input type="number" inputMode="decimal" value={text} min={min} max={max} step={step} disabled={disabled} aria-label={label}
          onChange={(event) => setText(event.target.value)} onBlur={commit}
          onKeyDown={(event) => { if (event.key === "Enter") commit(); }}
          className={`${fieldClass} num w-24 text-right`} />
        {suffix && <span className="text-[11px] text-zinc-400">{suffix}</span>}
      </span>
    </label>
  );
}

const ASR_LANGUAGE: Record<string, string> = { haas: "日本語（固定）", "parakeet-en": "英語（固定）", "auto-ja-en": "日本語・英語（区切りごとに自動判定）" };

function AsrSettings({ chosen, data, canEdit, save }: { chosen: string; data: SpeechEngines; canEdit: boolean; save: Save }) {
  const name = data.engines.find((e) => e.id === chosen)?.name ?? chosen;
  return (
    <div className={boxClass}>
      <div>
        <p className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-300">{name} の設定</p>
        {chosen === "whisper" ? (
          <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
            <span className="flex-1">聞く言語</span>
            <select value={data.selected.whisper_language} disabled={!canEdit} aria-label="whisper の聞く言語"
              onChange={(event) => void save({ whisper_language: event.target.value as SpeechSettings["whisper_language"] })} className={fieldClass}>
              <option value="ja">日本語</option><option value="en">英語</option><option value="auto">自動判定</option>
            </select>
          </label>
        ) : (
          <p className="text-xs text-zinc-500">聞く言語: {ASR_LANGUAGE[chosen] ?? "-"}</p>
        )}
      </div>
      <InputFilterSettings filter={data.selected.input_filter} meta={data.filter} canEdit={canEdit}
        onChange={(change, message) => save({ input_filter: change }, message)} />
    </div>
  );
}

const PRESET_LABEL: Record<InputFilter["preset"], string> = { recommended: "推奨", noisy: "騒がしい場所", quiet: "静かな部屋・小さな声", custom: "カスタム" };

/** 音声入力のフィルター。2026-11-19 利用者「音が小さい・ノイズが大きい場合を除去するフィルター。有効無効と詳細を変えられ、
 *  初めは推奨のプリセット」。推奨の中身は実測で決めた（docs/speech-input-filter.md） */
function InputFilterSettings({ filter, meta, canEdit, onChange }: {
  filter: InputFilter; meta: SpeechEngines["filter"]; canEdit: boolean;
  onChange: (change: Partial<InputFilter>, message?: string) => Promise<void>;
}) {
  const off = !canEdit || !filter.enabled;
  const gate = meta.gate;
  const dropped = gate.no_speech + gate.too_quiet;
  return (
    <div data-input-filter className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <Toggle label="入力フィルター" value={filter.enabled} disabled={!canEdit}
        hint="雑音・物音だけの区切りを捨てる"
        onChange={(value) => void onChange({ enabled: value }, value ? "入力フィルターを有効にしました" : "入力フィルターを切りました")} />
      {!meta.ready && filter.enabled && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300">未導入です。次に音声入力を使うと自動で入ります（2.8 MB）。</p>
      )}
      {(gate.passed > 0 || dropped > 0) && (
        <p className="num text-[11px] text-zinc-500">起動してから {gate.passed} 区切りを通し、声の無い {gate.no_speech}・小さすぎる {gate.too_quiet} を捨てました。</p>
      )}
      <div role="radiogroup" aria-label="入力フィルターのプリセット" className="flex flex-wrap gap-1.5">
        {(["recommended", "noisy", "quiet"] as const).map((preset) => (
          <button key={preset} type="button" role="radio" aria-checked={filter.preset === preset} disabled={off}
            onClick={() => void onChange({ preset }, `${PRESET_LABEL[preset]}にしました`)}
            className={`min-h-10 rounded-xl border px-3 text-xs disabled:opacity-50 ${filter.preset === preset
              ? "border-accent-500 bg-accent-50 font-medium text-accent-700 dark:bg-accent-600/15 dark:text-accent-300" : "border-zinc-300 dark:border-zinc-700"}`}>
            {PRESET_LABEL[preset]}
          </button>
        ))}
        {filter.preset === "custom" && <span className="self-center rounded-lg bg-zinc-100 px-2 py-1 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">カスタム</span>}
      </div>
      <details className="group">
        <summary className="flex min-h-9 cursor-pointer list-none items-center text-xs text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300">
          <span className="mr-1 transition-transform group-open:rotate-90">›</span>詳細な設定
        </summary>
        <div className="mt-2 space-y-2.5">
          <Toggle label="声の判定（Silero VAD）" value={filter.vad} disabled={off}
            hint="声の無い区切りを捨てる"
            onChange={(value) => void onChange({ vad: value })} />
          <NumberField label="声の判定の厳しさ" hint="大きいほど雑音に強い（推奨 0.5）" value={filter.vad_threshold}
            min={0.1} max={0.95} step={0.05} disabled={off || !filter.vad} onCommit={(v) => void onChange({ vad_threshold: v })} />
          <NumberField label="声とみなす最短の長さ" hint="短い物音・咳を捨てる" value={filter.min_speech_ms}
            min={0} max={3000} step={50} suffix="ms" disabled={off || !filter.vad} onCommit={(v) => void onChange({ min_speech_ms: v })} />
          <NumberField label="音量の下限" hint="-90 で使わない"
            value={filter.min_level_db} min={-90} max={-10} step={1} suffix="dB" disabled={off} onCommit={(v) => void onChange({ min_level_db: v })} />
          <label className="flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
            <span className="min-w-0 flex-1">雑音除去<span className="block text-[11px] text-zinc-400">日本語のみ効果あり</span></span>
            <select value={filter.denoise} disabled={off} aria-label="雑音除去" className={fieldClass}
              onChange={(event) => void onChange({ denoise: event.target.value as InputFilter["denoise"] })}>
              <option value="off">使わない</option><option value="gtcrn">GTCRN</option>
            </select>
          </label>
          <Toggle label="低い音のカット（80 Hz 以下）" hint="空調・うなり" value={filter.highpass} disabled={off}
            onChange={(value) => void onChange({ highpass: value })} />
          <NumberField label="マイクの感度（声とみなす音量）" hint="小さいほど小さな声も拾う（推奨 0.02）" value={filter.mic_sensitivity}
            min={0.002} max={0.2} step={0.002} disabled={!canEdit} onCommit={(v) => void onChange({ mic_sensitivity: v })} />
          <NumberField label="話し終わりの待ち時間（会話モード）" hint="黙ってから送るまで" value={filter.turn_end_ms}
            min={500} max={5000} step={100} suffix="ms" disabled={!canEdit} onCommit={(v) => void onChange({ turn_end_ms: v })} />
          <Toggle label="ブラウザの雑音抑制・エコー除去" hint="エコー除去など" value={filter.browser_processing} disabled={!canEdit}
            onChange={(value) => void onChange({ browser_processing: value })} />
        </div>
      </details>
    </div>
  );
}

const STEPS_LABEL: Record<number, string> = { 4: "速い", 8: "標準", 16: "高品質" };

/** TTS の設定と試し聞き。ここで選んだ声・速さ・品質・言語を AI アシスタントの読み上げも使う */
function TtsSettings({ engine, settings, canEdit, save }: {
  engine: SpeechEngine | undefined; settings: SpeechSettings; canEdit: boolean; save: Save;
}) {
  const show = useToasts((s) => s.show);
  const [text, setText] = useState("こんにちは。今日は良い天気ですね。");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => { if (audioUrl) URL.revokeObjectURL(audioUrl); }, [audioUrl]);
  if (!engine) return null;
  const speak = async () => {
    setWorking(true);
    try {
      const lang = settings.tts_language !== "auto" ? settings.tts_language : /[぀-ヿ㐀-鿿]/.test(text) ? "ja" : "en";
      const blob = await synthesizeSpeech({ text, lang });
      setAudioUrl(URL.createObjectURL(blob));
      window.setTimeout(() => void audioRef.current?.play().catch(() => undefined), 0);
    } catch (e) { show(e instanceof Error ? e.message : "音声を作れませんでした", "error"); }
    finally { setWorking(false); }
  };
  const row = "flex flex-wrap items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300";
  return (
    <div className={boxClass}>
      <p className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{engine.name} の設定</p>
      <p className="text-[11px] text-zinc-500">アシスタントの読み上げにも使います。</p>
      {/* 性別を選ぶと声をその性別に絞り、会話モードの話し方（一人称・語尾）もその性別に合わせる
          （2026-11-20 利用者「女性の声なのに男っぽい話し方をした。男性・女性を選べるように」） */}
      <div className={row}>
        <span className="flex-1">性別<span className="block text-[11px] text-zinc-400">会話モードの話し方もこれに合わせます</span></span>
        <div role="group" aria-label="声の性別" className="inline-flex rounded-xl bg-zinc-100 p-0.5 dark:bg-zinc-800" data-voice-gender>
          {([["F", "女性"], ["M", "男性"]] as const).map(([prefix, label]) => {
            const selected = settings.tts_voice.startsWith(prefix);
            const first = (engine.voices ?? []).find((v) => v.startsWith(prefix));
            return (
              <button key={prefix} type="button" aria-pressed={selected} disabled={!canEdit || selected || !first}
                onClick={() => first && void save({ tts_voice: first })}
                className={`min-h-9 rounded-lg px-4 text-xs font-medium ${selected
                  ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white" : "text-zinc-500"}`}>
                {label}
              </button>
            );
          })}
        </div>
      </div>
      <label className={row}><span className="flex-1">声</span>
        <select value={settings.tts_voice} disabled={!canEdit} aria-label="声" className={fieldClass}
          onChange={(event) => void save({ tts_voice: event.target.value })}>
          {(engine.voices ?? []).filter((v) => v[0] === settings.tts_voice[0]).map((v) => (
            <option key={v} value={v}>{v.startsWith("F") ? `女性 ${v.slice(1)}` : `男性 ${v.slice(1)}`}</option>
          ))}
        </select>
      </label>
      <NumberField label="速さ" value={settings.tts_speed} min={0.7} max={2} step={0.05} disabled={!canEdit} onCommit={(v) => void save({ tts_speed: v })} />
      <label className={row}><span className="flex-1">品質<span className="block text-[11px] text-zinc-400">高いほど遅い</span></span>
        <select value={settings.tts_steps} disabled={!canEdit} aria-label="品質" className={fieldClass}
          onChange={(event) => void save({ tts_steps: Number(event.target.value) })}>
          {[4, 8, 16].map((n) => <option key={n} value={n}>{STEPS_LABEL[n]}（{n}）</option>)}
        </select>
      </label>
      <label className={row}><span className="flex-1">読む言語</span>
        <select value={settings.tts_language} disabled={!canEdit} aria-label="読む言語" className={fieldClass}
          onChange={(event) => void save({ tts_language: event.target.value as SpeechSettings["tts_language"] })}>
          <option value="auto">自動</option><option value="ja">日本語</option><option value="en">英語</option>
        </select>
      </label>
      {engine.ready && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-zinc-600 dark:text-zinc-300">試し聞き</p>
          <textarea value={text} onChange={(event) => setText(event.target.value)} rows={2} maxLength={5000}
            aria-label="読み上げる文" className={`${fieldClass} w-full py-2`} />
          <div className="flex justify-end">
            <button type="button" onClick={() => void speak()} disabled={working || !text.trim()}
              className="min-h-10 rounded-xl bg-accent-600 px-4 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
              {working ? "作成中…" : "再生"}
            </button>
          </div>
          {audioUrl && <audio ref={audioRef} controls src={audioUrl} className="w-full" />}
        </div>
      )}
    </div>
  );
}
