# AI Resource Broker 詳細設計

最終更新: 2026-08-21
状態: PR-D1 core／PR-D2 adapter・Jobs移行実装済み

## 0. PR-D1 実装結果

PR-D1ではfake／実device collection、provider reservation/probe、lease lifecycle、有限queue、§20のcommon scheduling不変条件、管理API、Add-on owner cleanup、bounded telemetryまでを実装した。process再起動時はin-memory lease tableを空から開始するためstale leaseを復元しない。

実機ではsysfs-amdgpuから32GB GPUを発見し、exclusive requestのgrantと競合requestの`device_busy_exclusive`待機、renew/cancel/release後の予約0を確認した。NVMe上のQwen3.8 27Bをobserved supervisionのままcold-startした結果は次のとおり。

- Gateway request: 83.376秒（1 token）
- `cold_load_cost_sec` p90: 82.714秒（sample 1）
- first token latency: 0.565秒
- VRAM: load時29,269,970,944 bytes、stop後59,912,192 bytes
- 動的unload: 現行llama-server CLI/APIに操作なし。yield level 3はlevel 4（process stop）へ縮退

このsampleは実測値であり推定fallbackではない。`jobs.phase`／`jobs.wait_reason`のnullable列は単独migration `c83f7a19d2e4`で追加した。以下のPR-D2でmanaged supervision、thrash guard、Gateway lease、Jobs `waiting_resource`、OOM後の再実行制限まで実装した。

## 0.1 PR-D2 実装結果

Llama Gatewayは既存OpenAI互換URLとKV probeを維持したままBroker leaseを取得し、停止modelのcold-load見積り、起動済みmodelのprovider reservation、client disconnect／timeout／応答完了時の解放を一つのadmission経路へ統合した。resource-aware JobはBroker grant前にrunnerへ入らず、`queued + waiting_resource`のままcancel可能である。OOM profileは次回requestのVRAM floorと60秒cooldownへ反映する。

managed supervisionは既定OFFで、Gateway専用・local NVMe・実測cold-load p90ありの場合だけopt-inできる。推定処理時間がreload costの2倍を超え、最低常駐時間と5分2回のyield上限を満たす時だけdrain後に停止する。動的unload非対応の現行llama-serverではlevel 4のprocess stopを使い、新規LLM requestはdrainを取り消す。

実機の2回目のQwen3.8 27B cold-loadではGateway request 83.981秒、cold-load p90 83.038秒、first token 0.733秒、VRAM 29,269,983,232 bytesを観測した。20GiB exclusive Broker requestはLLM resident中に`device_busy_exclusive`で待機し、managed yield後59,924,480 bytesまで解放してgrantされた。request解放後のGateway requestは7.851秒でmodelを再起動した。sampleは2件のためmanaged既定化や汎用的な閾値調整の根拠にはせず、`observed`を既定のまま維持する。

## 0.2 reload cost profile（cold／warm分離）

thrash guardが比較するreload costは、起動後初回のdisk cold loadではなく、BrokerがLLMを停止した後に発生する再ロードである。page cacheを直接推測せず、同じ`residency_key`のstop記録から15分以内に開始した最初のloadだけを`warm`、それ以外を`cold`へ決定的に分類する。同じstopで2回をwarmにせず、別keyのstopは影響させない。互換上`cold_load_cost_sec`というsample field名は維持し、`load_kind`で意味を分ける。

`reload_cost_p90()`の選択規則は次のとおりで、推定値／catalog値へfallbackしない。

```text
warm >= 3 samples -> warm p90（yieldの正規basis）
warm < 3 and cold >= 3 samples -> cold p90（bootstrap中の保守的basis）
otherwise -> insufficient。自動yieldしない
yield threshold = selected p90 * 2.0
```

profileは`data_dir/resource-load-profiles.json`へschema version 1でatomic保存する。keyあたり最大50件、30日を超えるsampleは起動時に捨て、全体2MiBを超える場合は最終計測が古いkeyから除く。schema不一致、破損、型不一致はwarningだけを残して空profileとして起動し、Web起動を止めない。

warm sampleが無い初期状態はcold basisで保守的に動く。利用者が「今すぐ退避」を行い、続くGateway loadを3回実測するとwarm basisへ移る。このbootstrapを自動推定で短絡しない。

`estimated_runtime_sec`はresource要求元が申告する。ControlDeckはMedia固有の推定器を持たず、未申告の要求は`runtime_unknown`としてLLM退避を誘発しない。

## 1. 目的

ControlDeck の既存 LLM Gateway が持つ「空くまで待ってから通す」考え方を、LLM専用のKV制御から **LLM・VLM・画像・動画・3D・将来のAIワーカーで共有できるGPU/AIリソース受け入れ基盤**へ一般化する。

対象:

- llama.cpp / Ollama / SGLang 等のLLM
- embedding / reranker / VLM
- Media Forge image worker
- video/animation worker
- generative 3D worker
- 将来の音声生成/ASR/TTS等

Protocol gateway と resource scheduler は分離する。

```text
OpenAI-compatible LLM Gateway ----┐
Internal Chat / Workflow ---------┤
Media Forge Add-on ---------------┤
Future AI Add-ons ----------------┤
                                  ▼
                         AI Resource Broker
                                  │
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
              GPU 0             GPU 1          endpoint probe
          VRAM/compute        VRAM/compute      llama KV/slots
```

---

## 2. 現行実装から残すもの

現行 LLM Gateway の価値は維持する。

- OpenAI互換の単一接続先
- `auto` model routing
- 停止モデルのon-demand起動
- llama.cpp共有KV枯渇を投げる前に検出
- capacityが空くまで待つ
- clientが直接llama.cppを叩いて全体を巻き込むのを防ぐ

ただし現行 `prompt_chars/4 + max_tokens` と `llama.await_capacity()` は **llama.cpp KV専用 admission probe** として broker の下へ移す。

画像・動画にはtoken/KVの概念を強制しない。

---

## 3. Jobs と Broker の責務を分ける

### Jobs

ユーザー視点の work item を管理する。

- ID
- owner
- title/kind
- priority
- status
- progress/events
- result/error
- cancel
- DB persistence
- browser disconnect耐性

### Resource Broker

jobが必要とする有限resourceを取得できるか管理する。

- GPU device selection
- VRAM reservation/admission
- compute sharing mode
- model residency
- dynamic provider capacity
- wait queue / wakeup
- fairness
- lease expiry/release

### 重要

`waiting_resource` のjobを通常runnerの実行枠として数えない。

推奨フロー:

```text
Job created
  -> queued
  -> dependency check
  -> broker.acquire()
       ├─ granted -> starting -> running
       └─ wait    -> waiting_resource
  -> postprocessing
  -> validating
  -> succeeded/failed
```

CPU/network jobがGPU待ちjobにより詰まらないようにする。

---

## 4. Resource model

リソースを単一の「空きVRAM」数値だけで判断しない。

概念 resource dimensions:

```text
gpu.device
gpu.vram_bytes
gpu.compute_mode
model.residency
endpoint.dynamic_capacity
cpu.ram_bytes       (future)
cpu.threads         (future)
disk.temp_bytes     (future)
```

### 4.1 GPU lease

概念request:

```json
{
  "owner": "plugin:media-forge",
  "job_id": "abc123",
  "device": "auto",
  "vram": {
    "resident_bytes": 10000000000,
    "execution_peak_bytes": 15000000000,
    "cold_load_peak_bytes": 18000000000,
    "headroom_bytes": 1500000000
  },
  "compute_mode": "exclusive-preferred",
  "priority": 20,
  "class": "interactive",
  "residency_key": "image-worker:model-hash"
}
```

公開APIの詳細形は実装時に簡略化可。

### 4.2 VRAM estimate の批判的注意

モデル重みサイズ = 必要VRAM ではない。

考慮:

- runtime allocator overhead
- attention/activation
- VAE/encoder一時peak
- quantization workspace
- kernel cache
- fragmentation
- frameworkによるreserved memory
- load/unload中の一時二重保持

したがってmodel/runtime profileは最低でも:

```text
resident estimate
cold-load peak
execution peak
confidence/source
last observed actual peak
```

を持つ。

初回未知modelは保守的policyで通し、実測telemetryをprofileへフィードバックする。

---

## 5. Compute sharing mode

VRAMが入るから同時実行可能とは限らない。

worker/modelはsharing policyを宣言する。

```text
exclusive-required
exclusive-preferred
shared-safe
endpoint-managed
```

### examples

- 大型video model: `exclusive-required`
- image diffusion: 初期は `exclusive-preferred`
- llama.cpp: `endpoint-managed`（内部slots/KVで並列管理）
- 小型embedding: `shared-safe`候補

初期versionは保守的にし、実測なしで aggressive concurrency を有効化しない。

---

## 6. Dynamic admission probe

固定VRAM leaseだけでは provider 内部容量を表せない。

brokerにoptional provider-specific probe interfaceを持たせる。

```text
probe.check(request) ->
  accepting
  wait_reason
  capacity_snapshot
  retry_hint
```

### llama.cpp adapter

現行:

- `/slots`
- metrics
- KV pool used/free
- slot busy
- request token estimate

を使い、`endpoint.dynamic_capacity` として扱う。

LLM request は:

1. endpoint/modelがdevice上で利用可能であること
2. brokerのdevice/resource policy
3. llama KV/slot probe

の両方を通ってから upstreamへ送る。

### Media adapter

画像/動画workerは通常 dynamic KV probeを持たず、GPU lease + worker concurrency limitで制御する。

---

## 7. Queue / fairness

単純priority queueだけではbackground jobが永久に流れない。

使用する要素:

- base priority
- class weight
- queue age
- owner/plugin fairness
- resource fit
- residency affinity

推奨class:

```text
interactive
agent-interactive
workflow
batch
maintenance
```

### effective priority

詳細式は固定しないが、概念:

```text
effective = base_priority + bounded_age_bonus + class_weight
```

age bonusには上限を設け、低priority jobが最重要interactiveを追い越し続けないようにする。

### per-owner fairness

1つのagent/pluginが大量投入して他を独占しないよう、同ownerのrunning/waiting数を考慮する。

---

## 8. Resource fit と head-of-line blocking

queue先頭の巨大video jobがGPUに入らない間、小さなimage/LLM jobまで止めるのは避ける。

schedulerはstrict FIFOではなく、priority/fairnessを守る範囲で **fitするjobを選択**可能にする。

ただし巨大jobを永久に後回しにしないよう aging/reservation window を用意する。

将来:

- large-job reservation window
- maintenance window
- user指定「次はこのjobを優先」

を検討。

---

## 9. Model residency

load/unload時間は大きなコストなので、brokerは現在residentなmodel/runtimeを把握する。

```text
residency_key
device
owner/runtime
resident_vram
last_used
warm_until
load_cost_estimate
can_evict
```

### scheduling heuristic

公平性を壊さない範囲で:

- 同じresident modelを使う近接jobをまとめる
- idle warm window内は保持
- 大型高priority jobのため低priority resident modelをevict

### 注意

model router と resource broker の責務を混ぜない。

- router: どのmodel/capabilityを使うか決める
- broker: そのmodelをいつ/どのdeviceで動かせるか決める

---

## 10. Eviction

初期versionでは **running jobを強制preemptしない**。

安全にevict可能なのは原則:

- idle model
- warm resident cache
- stopped worker

running diffusion/LLM generationの強制VRAM evictionは破損/複雑化のリスクが高い。

将来 cooperative preemption/checkpoint対応runtimeのみ別途検討。

---

## 11. Lease lifecycle

```text
requested
waiting
granted
active
releasing
released
expired/canceled
```

API concept:

```text
acquire(request)
renew(lease_id)
release(lease_id)
cancel_request(request_id)
```

### lease safety

worker死亡・plugin disable・job cancel時にresourceが永久予約されないよう:

- TTL
- heartbeat/renew
- owner process health
- job correlation

を使う。

---

## 12. Queue visibility

UI/agentへ最低限返す:

```text
state = waiting_resource
reason = waiting_for_vram | waiting_for_compute | waiting_for_kv | waiting_for_model_eviction | waiting_for_device
queue_position_estimate
blocking_resources
selected_device (if decided)
```

正確な残り時間を無理に推定しない。

ControlDeck Jobs UIから「なぜ待っているか」が分かることを優先する。

---

## 13. Cancellation

### waiting

即座にqueue/requestを除去し、lease候補を解放。

### loading model

runtimeが安全にcancel可能なら停止。不可ならload完了後即evict/stop。

### running

既存job/provider cancel contractへ伝播。

plugin disable時もplugin ownerのwaiting leaseを取消す。

---

## 14. Multi-GPU

broker contractは最初からdevice IDを第一級にする。

```text
device=auto
preferred_devices=[]
forbidden_devices=[]
```

auto selection要素:

- compatibility
- free/admitted VRAM
- resident model affinity
- queue depth
- requested exclusivity

将来model parallel / tensor parallelは単一device leaseとは別の `device_set` resourceとして追加可能にする。

v1で無理にmulti-GPU model parallelを一般化しない。

---

## 15. Monitoring integration

broker telemetry:

- waiting requests
- granted/active leases
- per-device reserved estimate
- observed free/used VRAM
- active owners/jobs
- resident models
- wait reasons
- queue age
- admission failures
- OOM incidents
- estimate vs actual peak error

System Monitor/Models/Jobs/Add-on statusから再利用可能にする。

---

## 16. OOM circuit breaker

resource estimateが間違うことはある。

OOM発生時:

1. job失敗理由を正規化
2. runtime/model/device profileにincident記録
3. observed requirement/headroomを引き上げ
4. 短時間同条件の再実行を保守的に制限
5. 他running jobを巻き込んだ可能性をhealth check

無限auto-retryしない。

---

## 17. Add-on access

Add-on Platform v2 の `ai.resource_lease` capability経由のみ。

pluginは:

- 自分のjobに紐づくleaseだけ取得
- hostが許可したdevice/resource classのみ使用
- 他ownerのlease詳細は原則見えない

ControlDeck内部serviceはinternal broker APIを使用。

Media Forge固有APIをBrokerへ追加しない。

---

## 18. Migration from current LLM Gateway

### Phase 0 — interface extraction

現行 `llama.endpoint_capacity` / `await_capacity` を壊さず、broker adapter interfaceを追加。

### Phase 1 — LLM admission through Broker

LLM Gateway:

```text
resolve endpoint
ensure model ready
broker/admission request
  -> device policy
  -> llama dynamic KV probe
forward
release/request completion
```

既存 OpenAI-compatible URL は変更しない。

### Phase 2 — resource-aware Jobs

Jobsに `waiting_resource` と admission-before-runner を追加。

現行 `MAX_CONCURRENT` は CPU runner upper bound として残してもよいが、resource待ちはcountしない。

### Phase 3 — Media Forge lease client

image/VLM workerから利用開始。

### Phase 4 — video/3D + residency optimization

大型exclusive workload、model eviction/warm policyを実機データで調整。

### Phase 5 — other AI runtimes

Ollama/SGLang/ASR/TTS等を必要に応じて統合。

---

## 19. Failure isolation

Broker障害でControlDeck全体を起動不能にしない。

- broker internal error -> AI workloadを503/degraded
- monitoring/files/terminal等の非AI機能は継続
- stale lease recoveryを起動時実施
- provider health不明時はfail-openではなく workload policyに応じてfail-closed/isolated passthroughを明示

LLM KV保護のように、pass-throughすると他jobを破壊する経路ではfail-closedを優先。

---

## 20. Tests / acceptance

### Existing LLM behavior

- 現行KV capacity tests相当がbroker adapter後も通る
- `auto` routing互換
- gateway URL互換
- stream中継互換

### Common scheduling

- VRAM不足のMedia jobはOOMせず `waiting_resource`
- waiting jobは通常execution slotを占有しない
- resource releaseで適切なwaiterが起床
- cancel waiterは即除去
- priorityは効くがagingでstarvationしない
- fit schedulingで巨大waiterが小jobを無期限blockしない
- exclusive-required jobsは同一GPUで重ならない
- shared-safe workloadはpolicy範囲で並列可能

### Cross-workload

- resident LLMがある状態で大型image/video requestを正しくqueue/evict policy判定
- Media model resident中にLLM interactive requestがpolicy通り扱われる
- plugin disableでMediaのpending leaseが残らない

### Crash recovery

- worker crashでlease TTL回収
- ControlDeck restartでstale lease回収
- OOM後にcircuit breaker/profile補正

---

## 21. Architecture rule

**Gatewayはprotocolを扱い、Brokerは有限resourceを扱い、Jobsはユーザーの仕事を扱う。**

この3つを分離しつつ相関IDで接続する。

LLM GatewayをMedia Gatewayへ拡張するのではなく、両方が共通Brokerを使うことで、現在の受け入れ制御資産を汎用化する。
