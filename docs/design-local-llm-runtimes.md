# ローカルLLMランタイム（llama.cpp / Lucebox）設計

ControlDeckが常駐管理するローカルLLMランタイムは2つある。

| | llama.cpp | Lucebox |
|---|---|---|
| 配布元 | `souten-yd/llama-builder` | `souten-yd/AMDLucebox` |
| バイナリ | `llama-server` | `dflash_server` |
| ビルド種別 | Vulkan / ROCm 10 | ROCm 10（既定）/ ROCm 7.2 |
| 対応GPU | 汎用（Vulkan）/ AMD（ROCm） | Radeon AI PRO R9700（`gfx1201`）のみ |
| モデル | GGUF 1本（+ 任意のmmproj） | ターゲットGGUF + DFlashドラフトGGUF |
| 並列 | slot分割 + 共有KV | 単一セッション + 投機デコード |
| 導入先 | `data/runtimes/llama.cpp/` | `data/runtimes/lucebox/` |

両者は「systemdユーザーユニットで常駐するOpenAI互換サーバー + 別名(alias)で識別される
モデル設定」という同じ形をしている。この共通形をどこで束ねるかが本設計の要点。

## 層

```
                    OpenCode / チャット / ワークフロー / RAG
                                    │
                       ゲートウェイ  /api/v1/llm/v1     ← 全クライアントの共通接続先
                    （GPUリース → オンデマンド起動 → 受け入れ制御 → 転送）
                                    │
                          models_mgmt/local_llm.py      ← 唯一のランタイム分岐点
                          ┌─────────┴─────────┐
                    llama.py                lucebox.py
                          └─────────┬─────────┘
                          models_mgmt/gpu_release.py    ← 取得・検証・展開（共通）
                                    │
                          features/gpu_runtime.py       ← Addonの導入・更新UI
```

- **`local_llm.py`**: alias単位でランタイムを解決する。`list_instances()` は各項目へ
  `runtime` を付けて返し、`ensure_ready` / `start_instance` / `stop_instance` /
  `residency_key` / `await_capacity` を所有ランタイムへ委譲する。呼び出し側（ゲートウェイ、
  GPUブローカー、providerカタログ、OpenCode連携）はランタイム種別を知らない。
- **`gpu_release.py`**: GitHubリリースの取得、SHA256SUMS照合、安全な展開、`current`の
  張り替え、古い版の削除。`.tar.gz` と `.tar.zst` の両方を扱う。
- **`features/gpu_runtime.py`**: アドオン画面（設定 → オプション機能）から呼ぶ薄い
  アダプタ。`kind: "gpu-runtime"` として既存のfeature managerへ載る。

## alias とポートの一意性

alias はランタイムをまたいで一意にする。ゲートウェイのモデル名がaliasそのもので、
クライアントはランタイムを指定しないため。ポートも同様に、llama.cpp / Lucebox / Ollama
の三者で重複しないよう保存時に弾く（起動時のbind失敗は原因が見えにくい）。

## GPUリース

llama.cpp と Lucebox は同じ1枚のGPUを取り合うので、ブローカーから見て1つのprovider
（`local-llm`）が両方を代表する。residency key はランタイム別のプレフィクスを持つ
（`llama:` / `lucebox:`）。同じGGUFでもランタイムが違えばVRAM占有と再ロード時間が違うため、
実測値を混ぜない。

KV空き待ち（`await_capacity`）は共有KVを持つ llama.cpp だけの概念で、Lucebox では
素通しする。ここを揃えると、存在しないメトリクスを読みに行って毎回タイムアウト分だけ
生成開始が遅れる。

## ROCm 10 統一

`llama-builder` のROCmターゲットは `amdrocm-core-dev10.0-gfx1201` でビルドされるため、
ControlDeck側も ROCm 10 系として扱う（`llama.ROCM_SERIES_MAJOR`）。Lucebox の既定トラックも
ROCm 10（`lucebox.DEFAULT_TRACK`）。

ホストのROCmユーザースペースのメジャーが違う場合は、導入前・ランタイム選択前に警告するが、
導入自体は止めない。配布物のRPATHが `/opt/rocm/lib` を指し、実際に解決できる組み合わせも
あるため（本機ではROCm 7.2.1のホストでROCm 10ビルドが起動した）。判断材料を出したうえで
利用者に委ねる。

## Lucebox の推奨初期値

AMDLucebox READMEの実測プロファイル（180+ tok/s の条件）をそのまま
`lucebox.DEFAULT_INSTANCE` に置く。利用者が数値を詰めなくても動き始められるようにする。

| 項目 | 既定値 |
|---|---|
| `port` | 8216 |
| `max_ctx` | 131072 |
| `draft_block_size` | 16 |
| `cache_type_k` / `cache_type_v` | `q8_0` |
| `fa_window` | 2048 |
| `ddtree` / `ddtree_budget` | 有効 / 22 |
| `draft_residency` | `auto` |

## Lucebox のサンプリング制約（temperature）

DFlash2/DDTree の検証は**厳密グリーディ検証のみ**で、`temperature > 0` のリクエストは
投機経路を使わず自己回帰へ落ちる。実測で 142 tok/s → 29 tok/s（約1/5）。
`dflash_server` にサンプリング下で投機を有効にするフラグは無く、`--draft-block-size` を
学習時の値へ戻しても解消しない。ビルド側の性質として扱う。

`lucebox.DEFAULT_INSTANCE["prefer_speculative"]`（既定 True）が有効な間は、送信直前に
`temperature` を 0 へ固定する。判定は `lucebox.pins_greedy_sampling(alias=/port=)` に集約し、
2つの入口の両方で適用する。

- 内部チャット・ワークフロー: `LuceboxRuntimeProvider._payload()`（ポートで解決）
- 外部クライアント（OpenCode 等）: ゲートウェイの転送直前（alias で解決）

外部クライアントはこの制約を知らないので、ゲートウェイ側でも揃えないと OpenCode だけが
遅いままになる。llama.cpp の instance にはこの設定が無いため、`pins_greedy_sampling()` は
常に False を返し、共有KV側のサンプリングには一切触れない。

出力が決定的になる代償があるので、モデル個別設定でトレードオフを明示して切れるようにする。

### OpenCode が起こす場合の扱い

日本語チャット主体の利用者は `prefer_speculative` を切りたいが、OpenCode はコード生成が主用途で
投機デコードが 3〜5 倍効くため、切ると OpenCode まで遅くなる。そこで **OpenCode が停止中の
モデルを起こす場合だけ、個別設定より優先して投機ONで常駐させる**。

| 状況 | 方針 |
|---|---|
| OpenCode が停止中のモデルを起こす | 投機ON（個別設定より優先） |
| 他のクライアントが停止中のモデルを起こす | 個別設定に従う |
| 既に稼働中の常駐へ相乗りする | その常駐を始めたときの方針を引き継ぐ |
| 方針の記録が無い（再起動後・ゲートウェイ外での起動） | 個別設定に従う |

クライアント識別は、ControlDeck が生成する OpenCode の runtime config へ載せる
`x-control-deck-client: opencode` ヘッダで行う（`@ai-sdk/openai-compatible` の
`options.headers`）。User-Agent では OpenCode と利用者の自作クライアントを区別できない。
記録はプロセス内メモリ（`gateway._residency_greedy`）で、常駐が終われば次の起動時に決め直す。

## Lucebox の性能特性

速度 = 採択長 ÷ 約87ms。1ステップの単価は内容によらず一定なので、実効速度は
ドラフトがどれだけ当たるか（`avg_commit`）だけで決まる。自己回帰は内容によらず 29 tok/s で、
1ステップは自己回帰の約2.5トークン分にあたる。**採択長が2.5を下回る内容では投機デコードが逆効果**。

| 生成内容 | 採択長 | 投機ON | 自己回帰 |
|---|---:|---:|---:|
| 英語コード | 10〜13 | 92〜152 tok/s | 29 tok/s |
| 英語の文章 | 3.8〜4.2 | 42〜48 tok/s | 29 tok/s |
| 日本語の文章 | 2.1〜2.3 | 23〜25 tok/s | 29 tok/s |

DFlash2 ドラフトは英語・コード向けで、日本語では採択が伸びない。用途がコード
（OpenCode 等）なら大きく効き、日本語チャット主体なら `prefer_speculative` を切った方が速い。

`--max-ctx`・`--fa-window`・`--ddtree-budget`・ドラフトの量子化は、いずれも実測で改善しないか
逆効果だった（詳細は `implementation-status.md`）。唯一効くのは GPU 電力cap で、
210W → 300W（既定）で約9%。

## ツール利用（エージェント）での注意

`--fa-window` は 0（全注意）でなければならない。`dflash_server --help` にあるとおり
**0 より大きいと長いコンテキストでシステムプロンプトとツール定義が注意から外れる**。
OpenCode のプロンプトは 11,000 トークンを超えるため、2048 にすると引数の欠けた
ツール呼び出しや壊れた方言（`<function=...>` XML）が返り、パースに失敗して
中身の無い応答になる。エージェントは何もできずセッションを終える。

上流 Lucebox の `server/README.md` はスループット向けに 1024〜2048 を勧めているが、
それはツールを使わないベンチマークの話で、AMDLucebox の実測起動コマンドは
`--fa-window` を渡していない。デコード速度に差は無い（実測 86.2 → 85.9 ms/step）ので、
0 以外を選ぶ理由は基本的に無い。

`fa_window > 0` の instance は `runtime_status()["tool_warnings"]` で名指しし、
モデル個別設定の画面でも警告する。

`--agent-turn-cache`（`agent_turn_cache`、既定ON）は生成したツール呼び出しの先まで
prefix キャッシュを延ばす。無効だと `prefix_len` が初回プロンプトで止まり、ターンが
進むほど再 prefill が増える（実測: 20,081 トークンの3ターン目で 12.2秒 → 7.7秒）。
ツールを使わない用途では効かないだけなので既定で入れる。

## 導入物のレイアウト

Lucebox の配布物は `server/build/dflash_server` と
`server/build/deps/llama.cpp/ggml/src/**` の相対関係をRPATHで解決する。したがって
`current` は展開物の最上位ディレクトリ（`lucebox-r9700/`）へ向け、バイナリは相対パスで
覚える（`binary_relpath`）。バイナリのディレクトリを直接 `current` にすると同梱ライブラリを
見失う。llama.cpp は `bin/` 直下で完結するので従来どおりバイナリのディレクトリを指す。

## 更新

- `install`: このPCで動く構成を揃える（llama.cppは検出されたbackend全部、LuceboxはROCm 10）。
- `update`: 導入済み構成を最新リリースへ上げる。構成（backend / track）は変えない。
  取得・展開が全部終わってから `current` を張り替えるので、途中で失敗しても現行版は使える。
  直前の1版はロールバック先として残す（`RETAIN_VERSIONS`）。
- 稼働中のモデルは古いバイナリを掴んだままなので、更新の反映は次回起動から。

更新の有無は `/features/{id}/release-status` で確認する。一覧 `/features` はネットワークを
見ない（15秒ポーリングでGitHubの未認証レート上限を使い切らないため）。リリース情報は
`gpu_release` 側で5分memo化する。
