/** LLM の利用状況（並列・待ち行列・KV）。
 *
 * ターミナルを開かなくても「今何本使っていて、何本待っているか」が分かるようにする。
 * 共有KVでは slot が空いていても KV が尽きれば通せないので、両方を並べる。 */
import { useQuery } from "@tanstack/react-query";
import { getLlamaCapacity, type EndpointCapacity } from "../../api/models";

function tokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
}

/** 使用中スロットを丸で示す。数字だけより一目で埋まり具合が分かる。 */
function SlotDots({ busy, slots }: { busy: number; slots: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${busy} / ${slots} 使用中`}>
      {Array.from({ length: Math.min(slots, 12) }, (_, i) => (
        <span key={i}
          className={`h-2 w-2 rounded-full ${i < busy ? "bg-accent-500" : "bg-zinc-300 dark:bg-zinc-600"}`} />
      ))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-[4.5rem] shrink-0 text-[10px] text-zinc-400 md:text-[11px]">{label}</span>
      <span className="num min-w-0 flex-1 text-[11px] md:text-xs">{children}</span>
    </div>
  );
}

/** そのエンドポイントの混み具合を一語で返す（表示にも使う）。 */
export function endpointLoad(item: EndpointCapacity): "低" | "中" | "高" | null {
  if (!item.available || item.slots === 0) return null;
  const kvPct = item.usable > 0 ? (item.ctx_used / item.usable) * 100 : 0;
  if (item.deferred > 0 || item.busy >= item.slots) return "高";
  return item.busy > item.slots / 2 || kvPct >= 65 ? "中" : "低";
}

function EndpointRows({ item }: { item: EndpointCapacity }) {
  // モデル読込中などで /slots を取れないと slots=0 になる。
  // このとき 0/0 を「満杯」と読んでしまわないよう、状態表示に切り替える。
  if (!item.available || item.slots === 0) {
    return (
      <Row label="LLM並列">
        <span className="text-zinc-400">起動中…（読み込み待ち）</span>
      </Row>
    );
  }
  // 分母は窓の大きさそのもの。usable は「ここを超えたら新しい仕事を受けない」
  // という受付の線（既定で窓の 85%）で、入る量ではない。分母に出していたので
  // 65,536 で動かしているのに 55.7K しか入らないように見えていた。
  const kvPct = item.ctx_total > 0 ? Math.min(100, (item.ctx_used / item.ctx_total) * 100) : 0;
  // 色は受付の線を基準にする。線に届いたら赤、手前で橙。
  const admitPct = item.usable > 0 ? (item.ctx_used / item.usable) * 100 : 0;
  const tone = admitPct >= 100 ? "bg-red-500" : admitPct >= 75 ? "bg-amber-500" : "bg-accent-600";
  // 待ち行列と埋まり具合から、体感の混み具合を一語で示す。
  return (
    <div>
      <Row label="LLM並列">
        <span className="flex items-center gap-2">
          <SlotDots busy={item.busy} slots={item.slots} />
          <span>{item.busy} / {item.slots}</span>
        </span>
      </Row>
      {/* 前処理と生成は速さの桁が違う。どちらの最中かを添えないと、
          同じ「tok/s」が 20 倍ぶれて見える。 */}
      {item.phase === "prefill" && (
        <Row label="前処理中">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span>{item.prefill_tokens_per_second.toFixed(0)} tok/s</span>
            {item.prompt_tokens > 0 && (
              <span className="text-[10px] text-zinc-400">
                {tokens(item.prompt_tokens_done)} / {tokens(item.prompt_tokens)} 読み込み
              </span>
            )}
          </span>
        </Row>
      )}
      {item.phase !== "prefill" && (item.busy > 0 || item.tokens_per_second > 0) && (
        // 推論の中身は slot に出ず、応答の流れにしか現れない。ゲートウェイが
        // 中継の途中で見て記録している。
        <Row label={item.phase === "think" ? "思考中" : item.busy > 0 ? "生成中" : "生成速度"}>
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span>{item.tokens_per_second.toFixed(1)} tok/s</span>
            <span className="text-[10px] text-zinc-400">
              1本あたり {item.tokens_per_second_single.toFixed(1)}
            </span>
          </span>
        </Row>
      )}
      <Row label="待ち行列">
        {item.deferred > 0
          ? <span className="text-amber-600 dark:text-amber-400">{item.deferred} 件待機中</span>
          : <span className="text-zinc-400">なし</span>}
      </Row>
      <Row label="KV">
        <span className="flex items-center gap-2">
          <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
            <span className={`block h-full rounded-full ${tone}`} style={{ width: `${kvPct}%` }} />
          </span>
          <span>{tokens(item.ctx_used)} / {tokens(item.ctx_total)}</span>
        </span>
      </Row>
    </div>
  );
}

export function CapacityWidget({ compact = false }: { compact?: boolean }) {
  const { data } = useQuery({
    queryKey: ["llama-capacity"], queryFn: getLlamaCapacity,
    // 稼働中だけ短い間隔で追う。止まっていれば無駄に叩かない。
    refetchInterval: (q) => (q.state.data?.endpoints.length ? 3000 : 20000),
  });
  if (!data || data.endpoints.length === 0) return null;

  return (
    <section className={compact ? "" : "rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4"}>
      {!compact && <h2 className="mb-1 text-xs font-semibold md:mb-2 md:text-sm">LLM 利用状況</h2>}
      <div className="space-y-3">
        {data.endpoints.map((item) => (
          <div key={item.id}>
            {data.endpoints.length > 1 && (
              <p className="mb-0.5 truncate text-[11px] font-medium text-zinc-500">
                {item.running_alias} <span className="text-zinc-400">:{item.port}</span>
              </p>
            )}
            <EndpointRows item={item} />
          </div>
        ))}
        {/* 起動に失敗しているモデルは、読み込み待ちと取り違えないよう理由ごと出す。 */}
        {data.failed?.map((item) => (
          <Row key={item.alias} label="起動失敗">
            <span className="text-red-600 dark:text-red-400">
              {item.alias}
              {item.error && (
                <span className="ml-1.5 block text-[10px] break-words text-zinc-500">{item.error}</span>
              )}
            </span>
          </Row>
        ))}
        {/* OMo はエンドポイント個別ではなく全体の設定なので、まとめて1行だけ出す。 */}
        {data.omo && (() => {
          const loads = data.endpoints.map(endpointLoad).filter(Boolean) as string[];
          const load = loads.includes("高") ? "高" : loads.includes("中") ? "中" : loads[0] ?? null;
          return (
            <Row label="OMo負荷">
              {load ? (
                <span className={load === "高" ? "text-amber-600 dark:text-amber-400" : ""}>
                  {load}
                  <span className="ml-1.5 text-[10px] text-zinc-400">（{data.omo.model}）</span>
                </span>
              ) : (
                // OMoの対象モデルが停止していれば、負荷ではなくその事実を出す。
                <span className="text-zinc-400">
                  {data.omo.model ? `${data.omo.model} は停止中` : "—"}
                </span>
              )}
            </Row>
          );
        })()}
      </div>
    </section>
  );
}
