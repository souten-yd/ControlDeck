/** いま GPU に載っているものを、占有量の帯で並べて見せる。
 *
 * ホームからは VRAM の総量しか読めなかった。18GB 使われていることは分かって
 * も、それが LLM なのか画像モデルなのかが分からないので、次に何ができるかが
 * 判断できない。載っているものを実際の占有量に比例した帯で並べ、残りを一本の
 * 空きとして見せる。
 *
 * 出所は 2 つある。ControlDeck 自身の LLM runtime と、resource lease を取って
 * GPU を確保している利用者である。後者は lease だけで表現できるので、ここに
 * 個別の add-on の語彙は無い。名前は持ち主が自分で名乗る。
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

interface Resident {
  id: string;
  label: string;
  source: "runtime" | "addon" | "lease";
  owner: string;
  role: string | null;
  bytes: number;
  since_sec: number | null;
  state: string;
  job_id?: string;
  device_id?: string;
}

interface ResidentDevice {
  id: string;
  name: string;
  total_bytes: number;
  observed_used_bytes: number;
}

interface ResidentsResponse {
  devices: ResidentDevice[];
  items: Resident[];
}

/** 帯の色。役割ごとに固定し、更新のたびに入れ替わらないようにする。 */
const TONE: Record<string, string> = {
  runtime: "bg-accent-500",
  addon: "bg-emerald-500",
  lease: "bg-sky-500",
};

const SOURCE_LABEL: Record<string, string> = {
  runtime: "LLM",
  addon: "拡張機能",
  lease: "確保中",
};

function formatBytes(value: number): string {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

function formatElapsed(seconds: number | null): string {
  if (seconds == null) return "";
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}分`;
  return `${(seconds / 3600).toFixed(1)}時間`;
}

export function ResidentWorkloads() {
  const { data } = useQuery({
    queryKey: ["resource-residents"],
    queryFn: () => api<ResidentsResponse>("/resources/residents"),
    // 載せ替えは秒単位で起きる。止まった数字を見せるより取り直す。
    refetchInterval: 5000,
  });

  const device = data?.devices?.[0];
  const items = (data?.items ?? []).filter((item) => item.state !== "released");
  if (!device && !items.length) return null;

  const total = device?.total_bytes ?? 0;
  const observed = device?.observed_used_bytes ?? 0;
  // llama.cpp は自分の使用量を返さず、この機材では per-process の VRAM も
  // 読めない（KFD に PID が出ない）。つまり「どのモデルが何 GB か」は本当に
  // 分からない。分からないことと、何も載っていないことは違う: 申告の合計だけ
  // を見せると、29GB 使われている状態が「— / 32GB」になった。
  // 機材が実際に使っている量は分かるので、そちらを総量として使い、按分できない
  // 差分を「内訳不明」として 1 本の帯にする。
  const measured = items.filter((item) => item.bytes > 0);
  const unmeasured = items.filter((item) => item.bytes <= 0);
  const declared = measured.reduce((sum, item) => sum + item.bytes, 0);
  const held = Math.max(declared, observed);
  const unattributed = Math.max(0, held - declared);
  const free = Math.max(0, total - held);

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-zinc-500">GPUに載っているもの</h2>
        {total > 0 && (
          <span className="num text-[11px] text-zinc-400">
            {formatBytes(held)} / {formatBytes(total)}
          </span>
        )}
      </div>

      <div className="rounded-2xl border border-zinc-200 p-3 dark:border-zinc-700">
        {/* 全体の帯。占有量に比例させ、残りを空きとして残す。 */}
        {total > 0 && (
          <div className="mb-3 flex h-2.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
            {measured.map((item) => (
              <div
                key={`bar-${item.id}`}
                className={`${TONE[item.source] ?? TONE.lease} h-full`}
                style={{ width: `${Math.min(100, (item.bytes / total) * 100)}%` }}
                title={`${item.label} · ${formatBytes(item.bytes)}`}
              />
            ))}
            {unattributed > 0 && (
              <div
                className="h-full bg-zinc-400 dark:bg-zinc-500"
                style={{ width: `${Math.min(100, (unattributed / total) * 100)}%` }}
                title={`内訳不明 · ${formatBytes(unattributed)}（占有量を申告しないモデルの分）`}
              />
            )}
            <div className="h-full flex-1" style={{ minWidth: `${(free / total) * 100}%` }} />
          </div>
        )}

        {items.length === 0 ? (
          <p className="text-xs text-zinc-400">いま載っているモデルはありません</p>
        ) : (
          <ul className="space-y-2">
            {[...measured, ...unmeasured].map((item) => {
              const share = total > 0 && item.bytes > 0 ? (item.bytes / total) * 100 : 0;
              return (
                <li key={item.id} className="space-y-1">
                  <div className="flex items-baseline gap-2">
                    <span
                      className={`${TONE[item.source] ?? TONE.lease} h-2 w-2 shrink-0 rounded-full`}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{item.label}</span>
                    <span className="num shrink-0 text-[11px] text-zinc-400">
                      {item.bytes > 0 ? formatBytes(item.bytes) : "占有量の申告なし"}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 pl-4">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                      {share > 0 && (
                        <div
                          className={`${TONE[item.source] ?? TONE.lease} h-full rounded-full`}
                          style={{ width: `${Math.min(100, share)}%` }}
                        />
                      )}
                    </div>
                    <span className="num shrink-0 text-[10px] text-zinc-400">
                      {SOURCE_LABEL[item.source] ?? item.source}
                      {item.role && item.role !== "llm" ? ` · ${item.role}` : ""}
                      {item.since_sec != null ? ` · ${formatElapsed(item.since_sec)}` : ""}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {device && (
          <p className="mt-3 border-t border-zinc-100 pt-2 text-[10px] text-zinc-400 dark:border-zinc-800">
            {device.name} · 実測 {formatBytes(observed)} 使用中
            {unattributed > 0 && (
              // どのモデルの分かは本当に分からない。分からないと言い、
              // 量だけは実測から出す。
              <> · うち {formatBytes(unattributed)} は内訳を取得できません</>
            )}
          </p>
        )}
      </div>
    </section>
  );
}
