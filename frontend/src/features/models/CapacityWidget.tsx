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

function EndpointRows({ item, omoConcurrency }: {
  item: EndpointCapacity; omoConcurrency?: number;
}) {
  const kvPct = item.usable > 0 ? Math.min(100, (item.ctx_used / item.usable) * 100) : 0;
  const tone = kvPct >= 85 ? "bg-red-500" : kvPct >= 65 ? "bg-amber-500" : "bg-accent-600";
  // 待ち行列と埋まり具合から、体感の混み具合を一語で示す。
  const load = item.deferred > 0 ? "高"
    : item.busy >= item.slots ? "高"
    : item.busy > item.slots / 2 || kvPct >= 65 ? "中" : "低";
  return (
    <div>
      <Row label="LLM並列">
        <span className="flex items-center gap-2">
          <SlotDots busy={item.busy} slots={item.slots} />
          <span>{item.busy} / {item.slots}</span>
        </span>
      </Row>
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
          <span>{tokens(item.ctx_used)} / {tokens(item.usable)}</span>
        </span>
      </Row>
      {omoConcurrency !== undefined && (
        <Row label="OMo負荷">
          <span className={load === "高" ? "text-amber-600 dark:text-amber-400" : ""}>
            {load}
            <span className="ml-1.5 text-[10px] text-zinc-400">（論理並列 {omoConcurrency}）</span>
          </span>
        </Row>
      )}
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
            <EndpointRows item={item} omoConcurrency={data.omo?.concurrency} />
          </div>
        ))}
      </div>
    </section>
  );
}
