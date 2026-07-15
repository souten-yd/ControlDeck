# Model Runtime / AIアシスタント 詳細設計

最終更新: 2026-07-15

対象: Model画面、Ollama、llama.cpp、モデル登録、AIアシスタント、ワークフロー生成。
本書は `PLAN-power-jobs-runtime.md` の E/F およびチャット/生成残件を具体化する。

## 1. 再監査結果

### 実機能力

- OS: Linux。これは実行環境であり、ユーザーが選ぶruntimeではない。
- GPU: AMD dGPU 32GB。ROCmとVulkanを検出し、llama.cppの両backendを導入済み。
- Ollama 0.31.1: 稼働可能。
- llama.cpp: `llama-server` b10001系、ROCm/Vulkan、router mode、K/V別量子化、reasoning、
  speculative decoding（draft/MTP/ngram）、MoE CPU配置を実機`--help`で確認。
- 現在の選択はllama.cpp/Vulkan、Qwen3.6-27B Q5_K_M、ctx 2048、port 8090。

### UI/設定の不整合

- 設定sheetは常にOllamaタブから開き、現在選択中runtimeを表示しない。
- 「検出済みprovider」は状態badgeだけで選択操作になっていない。
- llama.cppの保存済み`ctx_size/n_gpu_layers/flash_attn`をフォーム初期値へ反映せず、
  開くたび4096/999/offになる。
- Model一覧・詳細・登録先がOllama固定。llama.cppは単一pathを別panelで起動するだけ。
- Ollamaのモデル設定とllama.cppのinstance設定が異なるUI階層にあり、共通概念と固有概念が混在する。

### 遅いチャットとワークフロー生成の再現

- 通常/永続チャットのOpenAI互換経路は出力token上限を送らず、`reasoning_content`を読まない。
  reasoning modelではユーザーからは無出力に見えるまま、context上限まで推論し続け得る。
- ワークフロー生成も`max_tokens`・schema制約・thinking制御なし。
- 実機再現: Qwen3.6-27Bで47秒、1161 tokenを推論だけに使いctx 2048へ達してHTTP 422。
- 同じprompt/modelで`enable_thinking=false`、`max_tokens=800`、JSON Schemaを指定すると
  11.77秒、305 completion tokenで有効JSONを生成した。

## 2. UX情報設計

設定を「環境 → 共通ポリシー → モデル」の3層にする。runtime固有の名称を最上位に混ぜない。

### 第1層: 利用環境 / runtime選択

実機検出で利用可能な選択肢だけをcard表示する。

- Ollama（内部backendはOllamaが自動選択）
- llama.cpp / ROCm
- llama.cpp / Vulkan

Linux/GPU/VRAMはcard上の環境情報とし、選択肢にはしない。選択中cardをradio + `使用中`で明示し、
未導入backendは同じ場所から導入する。選択は永続化する。

推奨はAMDではllama.cpp/ROCm。ただし実機benchmark/起動に失敗した場合はVulkanを提示する。
Ollamaを削除はしない。標準モードは**排他利用**とし、runtime切替時に他runtimeのモデルをアンロードして
VRAM競合と無駄な再ロードを防ぐ。上級者だけ**共存**を選べる。

### 第2層: 全runtime共通ポリシー

複数モデルで意味が同じ運用設定だけを置く。

- 既定runtime / 既定model
- runtime利用モード: 排他（既定）/共存
- アイドル自動アンロード: 全体on/off、既定分数
- 同時ロードモデル上限（排他時1、共存時はVRAM適合判定つき）
- モデルロード失敗時: 自動fit / 保守的fallback / 手動のみ
- チャット既定: 出力token上限、思考の既定（自動/なし/有効）、応答timeout

K/V量子化やGPU層数はruntime/モデル依存なので、ここには置かない。

### 第3層: モデル一覧と個別設定

providerを横断した共通モデルcardを使い、runtime badge、loaded、VRAM/RAM、context、量子化、能力を表示する。
登録時に対象runtimeを選択する。Ollamaは既存create/import、llama.cppはGGUF catalogへ参照登録する。

各モデルは次の順で設定する。

1. よく使う: context、出力token、思考、常駐/idle override。
2. 生成品質: temperature、top-k/top-p/min-p、repeat/presence/frequency penalty、seed。
3. メモリ/速度: GPU offload、batch/ubatch、parallel、Flash Attention、K/V別cache type。
4. 能力依存: MTP/speculative、draft model、MoE CPU配置、reasoning budget。
5. 上級: mmap/mlock、fit margin、threads、RoPE/YaRN、tensor split/override。

数値項目は頻出preset + 「カスタム入力」を共通componentで提供する。未対応optionは非表示、
危険/実験的optionは折りたたみ内で説明する。自由な`extra_args`は注入・重複flag・互換性問題を避けるため廃止し、
実機`--help`から許可された型付きoptionだけ保存する。

## 3. 設定の分類と能力判定

| 設定 | Ollama | llama.cpp | 表示条件 |
|---|---|---|---|
| context / output tokens | `num_ctx/num_predict` | `ctx-size/max_tokens` | 常時 |
| K/V cache | server全体同一type | K/Vを個別指定 | runtime capability |
| Flash Attention | server全体 | instance/model preset | flag検出 |
| GPU offload | `num_gpu` | `n-gpu-layers/fit` | GPU利用時 |
| MTP | Ollama自動・手動APIなし | `spec-type=draft-mtp` | GGUFに`nextn_predict_layers` + flag検出 |
| draft speculative | 非公開 | draft model + draft cache/層/token | 対応flag + draft model選択時 |
| MoE | runtime自動 | `cpu-moe/n-cpu-moe` | GGUF expert metadataまたは明示MoE |
| reasoning | model APIのthink | reasoning on/off/budget | chat template/能力検出 |
| idle override | keep_alive/exclude | managerがinstance unload | managed runtime |

llama.cpp optionは導入binaryの`--help`を正とし、名前・型・選択肢・説明をbackendから返す。
モデル能力はGGUF metadata（architecture/context/expert/next-token-prediction/chat template）を安全なread-only parserで取得する。
ファイル全体やtensorを読み込まずheader metadataだけを上限付きで読む。

## 4. データ/サービス設計

### RuntimePolicy

`data_dir/model-runtime-policy.json`へ次を保存する。

```json
{
  "selected_runtime": "llama.cpp",
  "selected_backend": "rocm",
  "coexistence": "exclusive",
  "idle_unload_enabled": true,
  "idle_unload_minutes": 30,
  "max_loaded_models": 1,
  "amd_gpu": {
    "enabled": true, "profile": "quiet", "power_limit_watts": 210,
    "memory_clock_mode": "limit", "memory_clock_level": 4,
    "core_clock_mode": "auto", "core_clock_level": 0
  },
  "default_model_ref": "llama.cpp:qwen3.6-27b",
  "chat": {"max_output_tokens": 2048, "reasoning": "auto", "timeout_seconds": 300}
}
```

APIは検証済みschemaだけを保存し、runtime切替・アンロード・ロードを監査する。

### AMD GPU 電力上限

AMD GPU の hwmon が `power1_cap` と範囲を公開する場合だけ、共通設定にスライダーを表示する。
実機では dGPU が `210–300 W`（既定300 W、現在210 W）を公開している。UI/APIの範囲は固定値ではなく、
選択中dGPUの `power1_cap_min` / `power1_cap_max` を µW から W へ変換した値を正とする。
設定無効時は変更せず、有効時はモデルロード直前に毎回冪等に適用する。

同じAMD dGPUが公開する`pp_dpm_mclk`も読み取り、周波数は連続sliderではなく実機DPM levelの選択肢にする。
このPCでは `96 / 456 / 772 / 875 / 1124 / 1258 MHz` で、idle時は既にautoで96 MHzまで低下する。
サーバー保存profileは次の4種類を提供する。

- `quiet`: 実機最小電力cap。MCLKは最大levelから1段だけ下げる（実機は1258→1124MHz）。SCLKは自動。
- `balanced`: 電力cap範囲の中間。MCLK/SCLKは自動。
- `full`: VBIOS既定電力cap。MCLK/SCLKは自動。
- `custom`: 電力capと、実機DPM levelによるMCLK/SCLK上限を個別選択。autoへ戻す操作も提供する。

既定profileでMCLKを下げるのはquietだけとし、balanced/fullへ切り替えた時は必ずautoへ戻す。
customはユーザーの明示操作としてMCLK/SCLKを変更できる。SCLK手動level適用はdriverの仕様通り
performance levelを`manual`へ移してから行い、autoへ戻す操作も提供する。
APU等でlevelが1個しかない、書込み非対応、対象dGPUでない場合はUIを表示しない。設定保存時にはlevel indexと
読取周波数を保持するが、起動前に現在のlevel一覧と再照合し、不一致なら適用せず明示エラーにする。

適用点は (1) 共通生成clientからの自動ロード前、(2) Ollama手動load前、(3) llama.cpp手動start前、
(4) llama.cpp systemd unitの `ExecStartPre` とする。これによりWeb再起動やOS起動時の自動起動にも同じ制限を適用する。
Control Deckを経由しない外部Ollama clientのロードは制御対象外であることをUIに明記する。

Webプロセスは一般ユーザーのままとし、sysfsへ直接書かない。`deck.sh service` が root所有の最小helperと
引数を限定したsudoers規則を導入する。helperは整数W、AMD hwmon、実機min/max、DPM levelを再検証し、
解決済み `/sys` パスが対象GPU配下にあることを確認してから `power1_cap`、
`power_dpm_force_performance_level`、`pp_dpm_mclk`、`pp_dpm_sclk`の4属性だけを書く。
任意パス・任意コマンド・shell実行は受け付けない。適用成功/失敗は監査ログへ残し、失敗時は
LLMを無制限で暗黙起動せず、ユーザーに修復手順を示してロードを中止する。

移行互換として、既にNOPASSWD設定済みの公式`amd-smi`がある環境では同じ検証済み配列引数で利用できる。
新規環境では広範な`amd-smi`全権限を付けず、`deck.sh service`の初回sudo認証で専用helperと限定sudoersを登録する。

### llama.cpp model catalog / router

llama.cppは公式router mode（`--models-preset` + `--models-max`）を利用する。モデル登録ごとに
alias/path/options/idle overrideをcatalogへ保存し、型付きINI presetを生成する。systemd unitはrouterを1つだけ起動し、
Webプロセスの子にはしない。旧単一`instance`設定は初回にcatalogへ移行し互換APIを維持する。

排他モードではllama.cpp load前にOllamaの`/api/ps`を列挙してkeep_alive=0、Ollama load前にllama unitを停止する。
共存モードでは停止せず、VRAM推定を超える場合に確認可能なエラーを返す。

### 共通生成client

チャット、Deep Search、ワークフロー生成、LLM nodeが同じprovider-aware request builderを使う。

- model個別設定 → runtime共通既定 → 安全既定の順でresolve。
- OpenAI互換streamは`content`と`reasoning_content`を別channelで処理する。
- すべての生成に有限`max_tokens`を設定する（通常既定2048、workflow JSON既定800）。
- workflow生成はthinking off、JSON Schema、finish_reason検証を強制する。
- schema非対応providerだけJSON object + parser/repairへfallbackする。
- 初回token時間、総時間、prompt/completion token、finish_reasonを秘密を含めず診断用に記録する。

## 5. AIアシスタントの独立機能化

名称は設定 `assistant.display_name`（既定「AIアシスタント」）として変更可能にする。
`/assistant`を独立routeとして追加し、ナビ/command paletteから開ける。ワークフロー画面の入口も互換のため残すが、
同じcomponent/Conversation DBを利用する。

- 会話一覧、切替、新規、名称変更、削除。
- chat/web/academic/deepを独立機能として提供。
- ワークフロー生成/実行は権限がある場合だけtoolsとして表示。
- 選択中runtime/model、thinking中、最初のtoken待ち、cancelを明示する。
- 生成/通常応答ともサーバージョブで継続し、ブラウザ再接続で復元する。

実装補足（2026-07-15）:

- `/assistant`を独立routeとして追加し、PCサイドバー、モバイル操作シート、command paletteから直接開く。
  ワークフロー画面内の既存入口は同じcomponentを使う互換導線として維持する。
- `RuntimePolicy.assistant_name`をdialog見出しと空画面へ反映する。会話一覧・切替・新規・改名・削除を
  server DBへ一本化し、削除は確認dialogと監査ログを必須にする。
- フロー生成は独立routeからも既存schema/semantic validatorを通し、品質表示後に登録または自動ビルドへ進む。

## 6. ワークフロー生成の修正と受入条件

生成schemaは`name/nodes/edges`、node typeはcatalog enum、必須構造を制約する。ただし各node configは種類ごとに異なるため、
第一段は共通object制約、第二段で既存engine/semantic validatorを使う。失敗時だけvalidator errorを付けて最大2回修正する。
プレビュー生成も一時HTTP処理ではなくjob/ChatMessageへ保存する。

受入条件:

- 実機Qwen3.6-27B + llama.cppで単純生成を30秒以内、有効JSON、登録可能にする。
- unknown node、未接続、誤ったtemplate参照をvalidatorが拒否し、自動修正できる。
- dry-runで副作用nodeを実行せず、入力解決・到達性・必要設定を確認してから登録/実行する。
- 失敗理由をUIに表示し、無出力のまま待たせない。cancelで生成を停止できる。

## 7. 実装順序

1. 共通生成client、chatの有限出力/reasoning stream、workflow schema生成修正と実機再試験。
2. RuntimePolicy API、能力card、選択状態、排他/共存、共通設定UI。
3. AMD電力上限の検出・最小helper・全load経路へのpreflight・条件付きUI。
4. llama.cpp catalog/router、登録先選択、モデル別型付き詳細設定、idle manager。
5. 独立AIアシスタントroute、会話管理、名称変更、生成job統合。
6. PC/320px、ROCm/Vulkan/Ollama各経路、複数model、再起動/idle/cancelを総合評価。

参照原本:

- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp speculative decoding: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md
- Ollama FAQ: https://github.com/ollama/ollama/blob/main/docs/faq.mdx
- Ollama environment options: https://github.com/ollama/ollama/blob/main/envconfig/config.go
- AMD SMI power control: https://rocm.docs.amd.com/projects/amdsmi/en/latest/doxygen/docBin/html/group__tagPowerControl.html
