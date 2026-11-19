/** Models の「TTS / ASR」タブ。音声認識と音声合成のエンジンを並べ、使うものを選び、導入し、試し聞きする。
 *
 * 2026-11-19 利用者「TTS と ASR は同じ項目に。ASR は Whisper ともう一つ（Haas-ja）、TTS はまず Supertonic 3。
 * 各 TTS / ASR の利用するエンジンを選択できるように」。 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getSpeechEngines, installSpeechEngine, selectSpeechEngine, synthesizeSpeech, type SpeechEngine, type SpeechKind } from "../../api/speech";
import { useAuth, useToasts } from "../../stores";
import { Skeleton } from "../../components/ui";

const KIND_LABEL: Record<SpeechKind, string> = { asr: "ASR（音声認識）", tts: "TTS（音声合成）" };
const LANG_LABEL: Record<string, string> = {
  ja: "日本語", en: "英語", ko: "韓国語", zh: "中国語", fr: "フランス語", de: "ドイツ語", es: "スペイン語",
  it: "イタリア語", pt: "ポルトガル語", ru: "ロシア語", vi: "ベトナム語", id: "インドネシア語", na: "自動（言語を問わない）",
};
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
  const manage = can("settings.manage");

  if (!data) return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;

  const select = async (kind: SpeechKind, engine: string) => {
    try {
      const next = await selectSpeechEngine(kind, engine);
      qc.setQueryData(["speech-engines"], next);
      show(`${KIND_LABEL[kind]} を ${data.engines.find((e) => e.id === engine)?.name ?? engine} にしました`);
    } catch (e) { show(e instanceof Error ? e.message : "切り替えに失敗しました", "error"); }
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

  return (
    <div className="space-y-4">
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
            {active && active !== chosen && (
              <p className="mb-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                選んだエンジンが未導入のため、{engines.find((e) => e.id === active)?.name} で動いています。
              </p>
            )}
            <div className="space-y-2">
              {engines.map((engine) => (
                <EngineRow key={engine.id} engine={engine} chosen={engine.id === chosen} canInstall={manage}
                  starting={busy === engine.id} onInstall={() => void install(engine)} />
              ))}
            </div>
            {kind === "tts" && engines.some((e) => e.id === chosen && e.ready) && (
              <TryTts engine={engines.find((e) => e.id === chosen)!} />
            )}
          </section>
        );
      })}
    </div>
  );
}

function EngineRow({ engine, chosen, canInstall, starting, onInstall }: {
  engine: SpeechEngine; chosen: boolean; canInstall: boolean; starting: boolean; onInstall: () => void;
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

function TryTts({ engine }: { engine: SpeechEngine }) {
  const show = useToasts((s) => s.show);
  const [text, setText] = useState("こんにちは。今日は良い天気ですね。");
  const [voice, setVoice] = useState(engine.voices?.[0] ?? "F1");
  const [lang, setLang] = useState("ja");
  const [speed, setSpeed] = useState(1.05);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => { if (audioUrl) URL.revokeObjectURL(audioUrl); }, [audioUrl]);

  const speak = async () => {
    setWorking(true);
    try {
      const blob = await synthesizeSpeech({ text, voice, lang, speed });
      setAudioUrl(URL.createObjectURL(blob));
      window.setTimeout(() => void audioRef.current?.play().catch(() => undefined), 0);
    } catch (e) { show(e instanceof Error ? e.message : "音声を作れませんでした", "error"); }
    finally { setWorking(false); }
  };
  const field = "min-h-10 min-w-0 rounded-xl border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-950";
  return (
    <div className="mt-3 space-y-2 border-t border-zinc-100 pt-3 dark:border-zinc-800">
      <p className="text-xs font-medium text-zinc-600 dark:text-zinc-300">試し聞き</p>
      <textarea value={text} onChange={(event) => setText(event.target.value)} rows={2} maxLength={5000}
        aria-label="読み上げる文" className={`${field} w-full py-2`} />
      <div className="flex flex-wrap items-center gap-2">
        <select value={voice} onChange={(event) => setVoice(event.target.value)} aria-label="声" className={field}>
          {(engine.voices ?? []).map((v) => <option key={v} value={v}>{v.startsWith("F") ? `女性 ${v.slice(1)}` : `男性 ${v.slice(1)}`}</option>)}
        </select>
        <select value={lang} onChange={(event) => setLang(event.target.value)} aria-label="言語" className={`${field} max-w-[10rem]`}>
          {(engine.languages ?? ["ja"]).map((l) => <option key={l} value={l}>{LANG_LABEL[l] ?? l}</option>)}
        </select>
        <label className="flex items-center gap-1 text-xs text-zinc-500">
          速さ
          <input type="number" min={0.7} max={2} step={0.05} value={speed} aria-label="速さ"
            onChange={(event) => setSpeed(Math.min(2, Math.max(0.7, Number(event.target.value) || 1.05)))}
            className={`${field} num w-20`} />
        </label>
        <button type="button" onClick={() => void speak()} disabled={working || !text.trim()}
          className="ml-auto min-h-10 rounded-xl bg-accent-600 px-4 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {working ? "作成中…" : "再生"}
        </button>
      </div>
      {audioUrl && <audio ref={audioRef} controls src={audioUrl} className="w-full" />}
    </div>
  );
}
