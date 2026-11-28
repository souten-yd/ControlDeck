# 計画: ローカル LLM のモデル登録（目録・取得・再開・フォルダ・設定の形）

状態: 2026-11-26 作成（Opus）。引き継ぎは `docs/HANDOFF-model-catalog.md`。

## 利用者の要望（2026-11-26）

1. Strata を最新版に更新し、Models の右上「＋」→ モデル登録で使えるモデルを増やす。
2. Maya も最新にしてから、使える最新のモデルを網羅的に入れる。
3. モデルは自動でダウンロードできるし、場所を選んでダウンロード済みのモデルを登録もできる。
4. Strata・Radiance・Maya のダウンロードは、止まっても（サービスの再起動後でも）途中から再開できる。
5. Strata と Maya に無検閲のモデルも足す。
6. Strata・Radiance・Maya はフォルダを選べて、ダウンロード済みのファイルから自由に選べる。
7. 3 つの設定項目の形を揃える。機能の多いものに合わせ、互いに無い機能は足す。合わせられない項目は枠ごと出さない。

## いまの状態（2026-11-26 に調べた）

| 項目 | Strata | Maya | Radiance |
|---|---|---|---|
| 版 | **0.2.22 に更新済み**（2026-11-26、エンジン 0.1.69。`strata components rollback` で戻せる） | v1.1.81（最新） | イメージ `stilldeadcode/radiance:2.2.0` |
| 登録画面の目録 | 4 系列だけ（`strata_gateway.py::_FAMILIES` と `Models.tsx::STRATA_SIZES` に二重に持つ） | Maya の setup を読む probe（15 件）。GLM-5.3（744B）と DeepSeek の `--ds4-quant`（unsloth の GGUF・EXL3・abliterated）は出ない | `.rad` を選ぶだけ |
| 取得 | 登録の時に Strata が取る | **取らない**（ディスクにあるものだけ登録。取得の命令を見せるだけ） | — |
| 途中からの再開 | Strata 任せ（未確認） | 「もう一度押すと続き」 | — |
| 再起動の後 | 実行中の Job は `interrupted` になるだけ（`main.py` の `recover_on_startup`）。自動では再開しない | 同じ | 同じ |
| フォルダ・ファイル選択 | GGUF の先頭ファイルを選べる | フォルダを選べる | `.rad` だけ |
| HF の取得（llama.cpp 用） | `hf.py::_download_file` は `.part` と Range で続きから取れる | | |

### 最新の Strata で動くモデル（公式 README・docs/MODELS.md、0.2.22）

| 系列（`--family`） | 量子化 | 備考 |
|---|---|---|
| `qwen`（ISTA-DASLab GSQ-RCO・推奨） | Q2_0・IQ2_XS・IQ3_XXS・IQ3_S | Qwen3.8-Flash-Next |
| `coder` | IQ1_M | コーディング向け |
| `swift`（Swift 1.5） | Q2_0・IQ2_XS・IQ3_XXS・IQ3_S | |
| `unsloth` | UD-IQ1_M・UD-Q2_0・UD-IQ2_XS・UD-Q2_K_XL・UD-IQ3_XXS・UD-Q3_K_XL・UD-IQ4_XS・UD-Q4_K_XL・UD-Q5_K_XL・UD-Q6_K_XL・Q8_0 | 指紋の一覧（`gguf_fingerprints.json`）にあるもの |
| `gyro`（agentionai） | Gyro-S・Gyro-M | 小さいが遅い |
| `qwen36`（Qwen3.6-35B-A3B） | UD-Q4_K_XL・UD-Q6_K_XL・Q8_0 | |
| `hy4-flash`（Tencent Hy4-Flash） | SQ-64・SQ-96・SQ-128（RAM の量で選ぶ） | 別エンジン `strata-hy4` |
| `inkling-2-mini` | SQ-20・SQ-15・SQ-10、UD-Q8_K_XL・UD-Q4_K_XL・UD-Q4_K_M・UD-IQ4_XS・UD-Q3_K_XL・UD-IQ3_XXS・UD-Q2_K_XL・UD-IQ2_XXS・UD-IQ1_M・UD-IQ1_S | 別エンジン。思考なし |
| `deepseek41` | ista-das-coder・ist-das-2.00bpw | エンジンは 0.1.59 で凍結 |
| `nvfp4` | NVFP4 | NVIDIA だけ（この PC では出さない） |
| 無検閲 | OrcaRouter `Qwen3.8-Flash-Next-Uncensored-GGUF` IQ3_XXS（85.2GB） | 公式の目録には無い。`iq_pack.py --compat-bf16` で変換し、手書きの run config で動かす（`docs/ORCA.md`）。RAM 約 50GiB |

### 最新の Maya で動くモデル（docs/MODELS.md、v1.1.81）

- GLM-5.3-Flash: Maya-S（96.5GB）・Maya-S24・Maya-XS（73.4GB）・Maya-M（116GB）・**Maya-M-Derisked（無検閲寄り。拒否を減らす重みの編集・実験的）**・Maya-L（156GB）
- GLM-5.3（744B）: Maya-XS（170GB）・UD-IQ2_M（239GB）— この PC（RAM 91GB + VRAM 32GB）では全部は載らない（SSD から読む。遅い）
- DeepSeek-V4.1-Flash-1017（`maya-ds4`）: Maya-S（340GB）・Maya-XS（登録済みの DS-XS）・unsloth GGUF（IQ1_M・IQ3_S・Q8_K_XL）・EXL3（2.25/2.9/3.5bpw）・**orcarouter の abliterated（無検閲。Mix-128G 約 114GB / Mix-256G）**。abliterated は gated（HF の規約の承諾とトークンが要る）・AMD は未検証
- MiniMax-M3.1-Flash: Maya-XS・Maya-S
- Hy4-Flash: Maya-XS・S・M・L

### Sly-Radiance（2026-11-27 追加）

`SlyBase/vllm-sly-radiance`（vLLM 0.32 の R9700 向け改造版）。Radiance と同じ形で登録・起動・停止・取得ができる。目録は `sly_radiance_catalog.py`（Swift-1.5 heretic MXFP4（標準・無検閲寄り）・AMD Quark AWQ・unsloth NVFP4・ParoQuant・先読み DFlash2。MiniCPM-V 4.7 は引数が違うので未対応として載せるだけ）。取得は HF の取得 Job（M3 で再開）、置き場所はモデルライブラリ。

## 方針

- **目録は 1 か所**に置く（backend）。画面は API から読む（今の `STRATA_SIZES` のような二重持ちをやめる）。
- 目録の各行: ランタイム・系列・量子化・表示名・大きさ・取得元・別エンジンか・思考の有無・**無検閲の印**・手作業が要るか・この PC に載るか（RAM と VRAM から計算）。
- ランタイムが自分の目録を持つもの（Maya の probe、Strata の指紋）は、そこから読む。読めない情報（大きさ・無検閲の印）だけ ControlDeck の表で足す。
- 取得の再開は**汎用の仕組み**にする（ランタイムごとに作らない）: 取得の Job に「再開できる」印と引数を残し、起動時に中断された取得の Job を自動で続ける（設定で切れる）。各ランタイムの取得器（Strata・Maya の setup・HF）が続きから取れるかは実測で確かめる。
- 設定の形: 共通の節（モデル・コンテキスト・同時に答える数・KV の形式・思考・メモリ・起動と停止・詳しい設定）に揃え、ランタイムに無い項目は出さない。

## 段

| 段 | 中身 | 状態 |
|---|---|---|
| M0 | Strata を 0.2.22 へ更新・起動の確認 | 更新済み（起動の確認は `HANDOFF` の作業記録） |
| M1 | Strata の目録を 1 か所にして全系列・全量子化を出す（無検閲は「手作業の変換が要る」印で案内） | 実装済み（実機の確認待ち） |
| M2 | Maya の目録に GLM-5.3・DeepSeek の ds4-quant を足す。Maya の取得（setup で取る）を画面から | 目録・画面ともに実装済み（#19・#24。実機の実重みは未確認） |
| M3 | 取得の Job の自動再開（再起動の後も）。Strata・Maya・HF・Radiance で実測 | 実装済み（#19・#27。実機の再起動での確認待ち） |
| M4 | 3 つとも「フォルダを選ぶ → 候補のファイルの一覧 → 選ぶ」 | 実装済み（#24 の共通部品 `RuntimeRegisterFromFile`。Models E2E は #29 で追随） |
| M5 | 設定の形を揃える（上の方針） | 実装済み（#24 の共通部品 `RuntimeSettings`。Models E2E は #29 で追随） |
| M6 | 無検閲: Strata の OrcaRouter の変換を Job で（取得 → `iq_pack.py` → run config）。Maya の Maya-M-Derisked・abliterated を目録に | 目録行は追加済み（#19）。OrcaRouter の手作業変換の Job 化は未実装（既存の手作業フローを維持） |

効果と処理時間（`AGENTS.md`「機能を取り込む条件」）: 目録の読み込みは画面を開いた時だけで、計算は RAM・VRAM の比べだけ。自動再開は起動時に 1 回 Job の表を読むだけ。どちらも測って PR に書く。

## run3 共通バックエンド API（2026-11-28 調査と方針）

Nexarch タスク #3（共通目録・取得・登録・設定 API と安全なルート）の調査まとめと API 契約。
UI 担当（共通部品・各パネル）はこの節の契約を使う。実測と出どころの区別は「source 確認 / runtime 実測」に従う。

### 既存の構造（2026-11-28 にコードで確認）

| 部品 | 場所 | 状態 |
|---|---|---|
| Sly 目録（唯一の情報源） | `backend/app/models_mgmt/sly_radiance_catalog.py`（ENTRIES 5 件 + MiniCPM 未対応） | そのまま共通目録へ集約 |
| Strata 目録 | `strata_catalog.py`（系列×量子化、fit・メモリ目安つき） | そのまま共通目録へ集約（系列×量子化を 1 行ずつに平らげる） |
| Maya 目録 | `maya_s.catalog()`（Maya 本体の probe を実行・件数と大きさは実データ） | probe は重いので共通目録 API では呼ばない。静的な追加行（Derisked）だけ載せ、全一覧は既存 `GET /api/v1/features/maya-s/catalog` を参照させる |
| HF 取得 Job（再開つき） | `hf.py::download` + `resume.py`（kind `model.hf_download`、`jobs.register_resumable`） | 共通取得の下請けとして再利用 |
| Job の終端状態 | `jobs/service.py`（成功・失敗・取消で再開記録を消す。停止中は interrupted を残す） | 「停止」をここへ最小限で足す |
| 2 ルート検証の実装例 | `commander/workdir.py`（realpath + ルート内 + data_dir/拒否領域の排除） | これと同じ考えで models 側に新規選択の検証を作る |
| 出口の守り | `web_collector/egress.py::guarded_client`（名前解決→全アドレス検査→検査済み IP へ接続。redirect は自動で追わない） | HF の通信（metadata・取得とも）をこれに載せる |

### 決めた意味（停止・再開・取り消し）

- **停止（stop）**: Job を中断し、**途中のファイル（`.part`）と再開の記録を残す**。Job の状態は `interrupted`、エラー欄に「停止しました（再開できます）」。再開の記録には `paused` 印を付け、**サーバー再起動後の自動再開では拾わない**（利用者が止めたものは利用者だけが再開する）。
- **再開（resume）**: 止めた Job の記録から同じ引数・同じ持ち主で新しい Job を作る。作成時刻と自動再開の回数は元の記録を引き継ぐ。元の Job の行には「Job <新id> で続きから再開しました」を残す。
- **取り消し（cancel）**: 既存の `POST /api/v1/jobs/{id}/cancel` のまま。**途中のファイル（`.part`）を消し**、再開の記録も消える（既存の終端状態の扱いと同じ）。HF の取得は明示的な取り消し以外では `.part` を残す（失敗・サーバー停止でも残す＝Range で続きから取れる）。
- Sly の **prefix cache は常にオン**。`sly_radiance.save_instance` と router の両層で `false` を 422 で断り、起動 argv は既存設定に `false` が残っていても常に `--enable-prefix-caching` を付ける。

### 共通 API の契約（run3 が実装・UI 担当が使う）

認証は既存と同じ（参照 `workflows.run`、変更 `workflows.edit`）。既存の各ランタイム API は削除しない（互換）。

1. **`GET /api/v1/models/catalog`** — 全ランタイムの推奨モデル目録（共通部品 RuntimeCatalog のデータ）。
   - `runtimes`: 各ランタイム `{id, name, installed, formats, acquire, entries[], catalog_ref?}`。`acquire` は `"hf"`（ControlDeck が HF から取得）/`"runtime"`（ランタイム自身が取得: Strata は登録＋初回起動の Job）/`"none"`。`catalog_ref` は probe など動的な全一覧への既存 API（Maya）。
   - `entries[]`: `{id, name, kind("model"|"drafter"), format("gguf"|"safetensors"|"rad"|"strata-quant"|"maya-quant"), repo, quantization, size_gib(null=未確認), memory_gib, uncensored, supported, note, fit("fits"|"tight"|"no"|"unknown"), source("catalog"|"hf")}`。
   - **`size_gib` が null の行は大きさを推測しない**。取得の開始時に HF の API から読む（下の 2・3）。
   - 載せる行は既存データだけ: Sly 5 件＋MiniCPM（未対応表示）、Strata は `strata_catalog` の系列×量子化（GSQ-RCO・Unsloth UD-*・Q8_0 を含む公式目録）、Maya は Derisked（`peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF`、repo id のみ・大きさ未確認）。llama.cpp は検索で選ぶ既存フローがあるので推奨行を置かない（確定した repo ができてから足す）。
2. **`GET /api/v1/models/catalog/{runtime}/entries/{entry_id}/files`** — repo のある行の、取得直前のファイル一覧（既存の Sly と同じ形: `{repo, files[{path,size}], total_bytes, libraries}`）。HF への metadata 問い合わせで、外部許可（`ensure_external_allowed`）と出口の検査（guarded transport）を通る。
3. **`POST /api/v1/models/acquire-jobs`** — 共通の取得開始。`{runtime, entry_id, library_id, files[]?, register?}`。
   - `files` 省略時は repo の最上位ファイル全部。**大きさ（expected_bytes）はクライアントから受け取らず Job の開始時に HF の API から読む**（repo に無いファイルは 422 相当の失敗にする）。試験は偽の HF。
   - `register` は取得後に登録する場合だけ: `{alias, port?, context?, max_num_seqs?, kv_cache?, role?, endpoint_id?, ctx_size?, n_parallel?}`。runtime 別に adapter が使う欄だけ反映（llama は `llama.save_instance`、sly-radiance は `sly_radiance.save_instance(model_path=保存先フォルダ)`）。省略時はダウンロードのみ。
   - 返り値 `{job_id}`。Job は既存の再開可能な `model.hf_download`（再起動後に続きから）。監査は `model.acquire`（runtime・entry・repo・件数・library・alias。トークンは記録しない）。
4. **`POST /api/v1/jobs/{job_id}/stop` / `POST /api/v1/jobs/{job_id}/resume`** — 上の意味どおり。`GET /api/v1/jobs` と `GET /api/v1/jobs/{id}` に `can_resume`（再開の記録が残っているか）を足す。取り消しは既存 `cancel` のまま。
5. **`GET /api/v1/models/instances`** — 全ランタイムの登録済みモデル一覧を共通の形で（`{runtime, alias, model_path, format, size_bytes, loaded, port, ctx_size, n_parallel, base_url}`）。既存の各一覧 API はそのまま。
6. **`GET /api/v1/models/register/inspect?path=...`** — ファイル/フォルダから登録するときの読み取り。
   - **新規選択のルートは `/data1tb` と `/data2tb` だけ**（realpath・symlink 検証・拒否領域の排除）。既存の他ルートの登録は既存 API で表示・再保存・起動できて壊さない（この API は新しく選ぶときだけ使う）。
   - 返り値: `{path(realpath), kind, format("gguf"|"safetensors"|"rad"|"unknown"), alias_suggestion, size_bytes, metadata{quantization, context_length, quantized_by, architecture, model_type...}, runtimes[{runtime, eligible, reason}]}`。GGUF はヘッダだけ・件数と読取量を制限して読む（config.json は 2MB まで。event loop を止めないため同期 endpoint）。
   - 登録そのものは既存の各ランタイム API（`POST /models/llama/instances`・`POST /models/sly-radiance/instances`・`POST /models/radiance/instances`・`POST /models/strata/instances`・Maya の register-jobs）をこの値で呼ぶ。
7. **`GET /api/v1/models/settings/capabilities`** — 設定の共通の形（UI 担当の RuntimeSettings 用）。ランタイムごとに節 ①モデル ②コンテキスト ③同時に処理する数 ④速さの調整 ⑤起動 ⑥エキスパートの並びで、各欄 `{id, label, type, choices?, min?, max?, step?, default, expert}`（`step` は整数の刻み。例: Strata の `kv_resident` は 1024）。**ランタイムに無い欄は返さない**（Sly の prefix cache は欄として出さず `always_on` に注記）。欄の id は実際の保存 API（`save_instance`・`StrataModelBody` など）の引数名と同じで、選択肢・上限はその検証定数から読む（二重持ちをしない）。
   - **Maya の欄（run4 で是正）**: 登録は `MayaRegisterBody`（`setup_id`・`folder`・`context`・`gpu_index`）、調整は `MayaInstanceSettingsBody`（`parallel`・`reasoning_effort`・`thinking_budget` ほか）。`alias` は目録の `setup_id` から Maya 自身が決めるので欄に出さない。`context` の下限は両 API が受ける **8192**、`parallel` は **1〜4**。`reasoning_effort` の選択肢は `MayaInstanceSettingsBody` の `Literal` から読む（`Optional` でも取れる `_literal_choices`）。同じ id を 2 節に出さない。
   - **Strata の欄（run4 で是正）**: 量子化の選択肢は既定系列（`qwen`）の `strata_catalog.quantizations()` から読む（系列を変えたら目録 API で選び直す）。系列の表示可否は `strata_catalog.visible(family, nvidia)` を 1 回の GPU 判定で評価する。

### 新規の外部ルート（route_map）

- HF の metadata 問い合わせ・取得・再開の全通信を `hf.py` から guarded transport（`web_collector/egress.py`）で出す。`ext-hf-download` の注記（「ensure_external_allowed を通らない」）は解消して書き直す。
- 新規の内部 API（catalog・acquire・instances・inspect・capabilities・jobs の stop/resume）は `ui-api-models` の中（owner は既存の `models_mgmt/router.py`・`jobs/router.py`）。

### run3 の作業一覧

実装しながら状態を更新する（済んだら「済」に変える）。

| 作業 | 状態 |
|---|---|
| PLAN への契約記録（この節） | 済 |
| HANDOFF の更新・CLAUDE.md からのリンク | 済 |
| jobs 基盤: stop/resume/cancel の意味分離 + can_resume | 済 |
| hf.py: guarded 出口・redirect 検査・開始時 metadata・`.part` の保持 | 済 |
| sly prefix cache 常時オン（両層） | 済 |
| 共通目録・取得・一覧・inspect・capabilities API | 済 |
| 試験（既存拡張 + 新規 run3 分） | 済（関連9ファイル＋新規1ファイル、下記） |

run3 の実装（2026-11-28）:

- 目録は `backend/app/models_mgmt/runtime_catalog.py` に集約し、Sly の既存目録・`strata_catalog`・Maya の probe から読む（二重持ちしない）。大きさが未確認の行は `size_gib=null`。
- 取得は `hf.download` の `fetch_sizes=True`（開始時に HF API から大きさを読む。`files=[]` は repo の最上位ファイル全部）と `_open_stream` の redirect 検査、`.part` の保持（停止・サーバー停止・失敗）を実装。停止は `jobs.stop` が `interrupted` と `paused` 印を残し、`resume` は保存引数から新しい Job を作る。
- 共通 API は `models_mgmt/router.py` の `GET /catalog`・`/catalog/{runtime}/entries/{entry_id}/files`・`POST /acquire-jobs`・`GET /instances`・`/register/inspect`・`/settings/capabilities`。既存 API は残した。
- 試験: 新規 `backend/tests/test_runtime_catalog.py` 16 件。既存は `test_strata_catalog`・`test_sly_radiance`・`test_hf_download`・`test_maya_register`・`test_radiance_runtime`・`test_maya_probe`・`test_strata_memory`・`test_route_map`・`test_jobs_persistence` を拡張。

### run4 の作業一覧（2026-11-28・検証と契約の是正）

run3 の実装を差分で読み、依頼の「事前レビュー run4 の必須条件」と照合して直した。既存 API の削除・画面の変更はしていない。

| 作業 | 状態 |
|---|---|
| `hf.py` の redirect で HF のトークンを別ホスト（CDN）へ送らない・https 以外を断る | 済（試験追加） |
| repo・revision・files の正規表現検証（`..`・絶対パス・制御文字の拒否） | 済（試験追加） |
| Sly の prefix cache を router と `save_instance` の両層で false 拒否 | 済（試験追加） |
| Radiance の `.rad` と Sly の safetensors の相互誤登録防止 | 済（試験追加） |
| Maya の設定 capability を実際の保存 API に一致（context 下限 8192・parallel 1〜4・欄 id の重複解消・`setup_id` に） | 済（`test_capability_fields_match_the_save_api` に maya を追加） |
| Strata の量子化の選択肢を系列に合わせる・GPU 判定を 1 回に | 済 |
| 差分レビュー: `AcquireRegister` を Sly の保存範囲へ（取得後の登録失敗を防ぐ） | 済（試験追加） |
| 差分レビュー: Maya の壊れた `settings.json` で設定 API を 500 にしない | 済（試験追加） |
| 差分レビュー: `kv_resident` の刻み・Sly 登録ポートの空き探索・`list_model_files` の二重呼び出し・再開の二重実行の印・`can_resume` の実在確認・既定値・ファイル名の `?` `#` `%` | 済 |
| 再レビュー: 登録の欄をランタイムごとに検査（他ランタイムの欄は 422） | 済（試験追加） |
| 再レビュー: ポート衝突は例外の印で判定・停止したままの記録は 30 日で整理 | 済（試験追加） |
| 確認: 関連 17 ファイル 278 件合格 | 済（下記） |

run4 で確認したこと（コードを読んだ結果。実機・実重みは未確認）:

- `.part` は停止・失敗・サーバー停止で残り、明示的な取消だけが消す。`paused` 印の付いた記録は起動時の自動再開で拾わず、`resume` は期限（7 日）と回数（3 回）の歯止めを引き継ぐ。
- metadata の問い合わせと再開も `ensure_external_allowed` と guarded transport を通る。保存先・`.part` の symlink は拒否。GGUF/config は読む量と件数を制限。inspect は同期 endpoint（threadpool）で event loop を止めない。
- 設定値は既存の保存 API（llama/radiance/strata/sly は `test_capability_fields_match_the_save_api` が全欄を照合。Maya も run4 から対象）と一致する。argv/YAML への往復は `test_radiance_runtime`・`test_strata_memory`・`test_sly_radiance` で確認済み。
- **意図的に空にした目録行**: llama.cpp（確定 repo が無く、既存の検索・ファイル選択の流れがある）と Radiance（`.rad` 登録の流れがある）は推奨行を置かない。目録 API の `entries` は空で、`acquire` と `catalog_ref` で既存フローへ案内する。依頼「全 5 形式の adapter と一覧を backend に一本化」に対しては、一覧（instances）と登録の受け口は 5 ランタイム分を出し、**取得目録の行だけ**を既存フローに委ねる判断である。

試験（隔離: `env -u CONTROL_DECK_CONFIG`、`/data1tb/ControlDeck/app-strata/.venv`）:

- `test_runtime_catalog.py` 22 passed（maya の契約・id 重複・既定値・取得引数の範囲・壊れた Maya 設定・欄の適用範囲の検査を追加）。
- `test_jobs_persistence.py` 29 passed（再開の同時実行・停止の記録の期限の検査を追加）。
- 関連 17 ファイル（runtime_catalog・hf_download・jobs_persistence・sly_radiance・strata_catalog・strata_gateway・strata_optional・strata_memory・local_llm_gateway・radiance_runtime・models_local_register・maya_register・maya_probe・maya_s_addon・llama_runtime・route_map・features）: 278 passed。
- 全件試験は禁止のため未実行。実重みの HF 取得・実機の登録/起動・Frontend/E2E は未確認（偽 HF・隔離）。

実装後にこの表と `docs/HANDOFF-model-catalog.md` の作業記録を更新する。

M3 の載せ方（M2・M4 で Maya・Radiance の取得 Job を作るとき）: 取得の Job を `jobs.create(..., resume={JSON にできる平らな引数})` で作り、`app/models_mgmt/resume.py` と同じ形で `jobs.register_resumable(kind, factory, title=...)` をモジュールの読み込み時に登録する（factory は保存した引数から runner を作る）。秘密値（HF のトークン等）は resume に入れない。再開の記録・回数の歯止め（3 回・7 日）・停止時の扱いは Job 基盤が持つので、ランタイムごとに作らない。

## 設定項目の調査（2026-11-28）

Nexarch タスク #6。前回の explore（run1/run2）が Web で調べた内容を、依頼どおりこの計画へ永続化する。
run2 は読み取り専用だったため未書き込みだった。この作業も Web の閲覧と既存ファイルの読み取りだけで、
本番の checkout・data・モデル・プロセス・workflows 画面は変更していない。モデルや npm/pip の取得はしていない。

確かさの記号（`AGENTS.md`「分からないことの調べ方」に従い分けて書く）:

- **【公】**: 公式 README / docs / CLI help に書かれていたもの（2026-11-28 に Web で確認）。
- **【源】**: リポジトリのコード・保存 API の検証定数で確認したもの（run3/run4 の確認を再利用）。
- **【未】**: この PC の本番では未実測。公式の既定値を引用しただけで、挙動は確かめていない。

### ランタイム × 設定項目

| 項目 | Radiance（C++ エンジン / `stilldeadcode/radiance:2.2.0`） | Sly-Radiance（vLLM 0.32 の R9700 向け改造版） | Strata（0.2.22） | Maya（v1.1.81） | llama.cpp |
|---|---|---|---|---|---|
| コンテキスト長 | **【公】** `--max-model-len`（Flash-Next 200000、27B 200000、MiniCPM5 131072、bf16 参照 32768） | **【公】** `--max-model-len`（quickstart 262144、最速起動 65536）。vLLM は `k/m/g` と `auto` も可 | **【公】** `--max-context`（全 Window は 262K。IQ3 系は 128K を推奨設定で案内） | **【公】＋【源】** `--max-context`。保存 API の下限は 8192（run4 がコードで確認） | **【公】** `-c, --ctx-size`（既定 0＝GGUF のメタデータから読む） |
| 同時に処理する数 | **【公】** `--max-num-seqs`（Flash-Next と 27B-bf16 は 8、その他 32） | **【公】** `--max-num-seqs 8`（起動例） | **【公】** `--batch` スロット／`--parallel`（スロット数。通常 1） | **【公】＋【源】** `--parallel N`（1〜4 を保存 API が受ける）。`MAYA_PAR_MAX` でサーバー側の上限も可 | **【公】** `-np, --parallel`（既定 -1＝自動。スロット数） |
| KV キャッシュ形式 | **【公】** `--kv-cache-dtype fp8`（各 compose の共通設定）。`rad-convert --kv-cache-dtype bf16\|fp8` は変換時の宣言幅 | **【公】** `--kv-cache-dtype fp8`（既定）、オプトインの 4bit `kv4`（TetraAttention）、`--mamba-ssm-cache-dtype bfloat16`、ネイティブ 4bit は `ultraquant_4bit` | **【公】** `--kv int8`（既定）／`q4_0`（Hadamard 回転＋4bit）／`k8v4`。`--kv-streaming on\|off\|auto` と `--kv-resident 32768`、`--kv-grow` | **【公】** `MAYA_GLM_KV_INT8`（v1.1.55 から既定オン、`--kv int8`）。`--kv fp16` で戻す。`--kv-streaming`／`--kv-resident 32768` | **【公】** `-ctk/-ctv, --cache-type-k/-v`（f32, f16, bf16, f8_e4m3, q8_0, q4_0, q4_1, iq4_nl, q5_0, q5_1。既定 f16） |
| prefix cache | **【公】** README が Flash-Next の「prefix-cache tiers」に言及。個別のフラグ名は `docs/GUIDE.md` 参照＝**【未】** | **【公】** `--enable-prefix-caching` / `--no-enable-prefix-caching`（最速起動では off）。**【源】** ControlDeck は常時オンで `false` を 422 にする（run3/run4）。欄自体を出さない | **【公】** 会話キャッシュ（GPU→ホスト→SSD の階層）。固定 prefix を使う `strata_prefix` は `--prompt-cache 3` 以上が必要 | **【未】** `docs/SETTINGS.md` に prefix cache の専用スイッチの記載なし。KV streaming が相当するが、等価かは未確認 | **【公】** `--cache-prompt`（既定オン）、`--cache-reuse N`（KV shifting の最小チャンク、既定 0＝無効）、`--cache-ram 8192`、`--ctx-checkpoints 32` |
| 投機的デコード | **【公】** `--num-speculative-tokens`（Flash-Next 3、bf16 参照 0）。ドラフターはコンテナに同梱 | **【公】** `--speculative-config.method dflash` / `.model syvai/Qwen3.8-27B-DFlash2-W4A16` / `.num_speculative_tokens 7` / `.draft_sample_method greedy\|probabilistic` | **【公】** `--mtp`（モデル自身の MTP 層）＋`--spec N` / `--spec-min-p P`（setup 既定は 4 / 0.5）＋prompt lookup | **【公】** モデル同梱の MTP／DFlash2 ドラフト。`MAYA_GLM_NO_MTP=1` で無効、`MAYA_GLM_SPEC_*` で細かく制御 | **【公】** `--spec-type`（none / draft-simple / draft-eagle3 / draft-mtp / draft-dflash / draft-dspark / draft-dflash2 / ngram-*）。`--spec-draft-model`、`--spec-draft-n-max 3` |
| 量子化 | **【公】** `rad-convert --recipe` / `--quant`（`rtn`・`gptq`・`awq`・`autoround`・`paroquant`・`ggml`・`cast`）で `.rad` コンテナを作る | **【公】** MXFP4 GPTQ（compressed-tensors）、`--quantization quark`（Quark AWQ）、`RADIANCE_NVFP4_MXFP4=1 --quantization compressed-tensors`、ParoQuant／AutoRound／escha | **【公】** ファミリー×量子化パック（GSQ-RCO の Q2_0 / IQ2_XS / IQ3_XXS / IQ3_S、Unsloth UD-*、Q8_0）。`--family` で選ぶ | **【公】** Maya-XS / S / S24 / M / L、Maya-M-Derisked、UD-IQ2_M、EXL3。`--model <setup id>` で選ぶ | **【公】** GGUF の量子化をファイル側で選ぶ（Q4_K_M・Q8_0・IQ2_XXS など）。実行時の切替ではない |
| GPU メモリ割合／配分 | **【公】** 割合ではなく `--host-pool-mib 12288`・`--gpu-headroom-mib 96`・`--expert-vs-cache-ratio 0.82`・`--placement expert_tiered`・`--weights-disk-tier` | **【公】** `--gpu-memory-utilization 0.98`（起動例）。vLLM の既定は extensible KV なら 1.0、それ以外 0.92 | **【公】** `--vram-reserve-mib`、`--resident-budget-gib N`、`--pcie-frac`、エキスパートキャッシュの予算と KV の取り合い | **【公】** `MAYA_GLM_RAM_HEADROOM_GB 6`／`MAYA_GLM_RAM_GB`／`--vram-reserve-mib`／`MAYA_GLM_SPLIT auto` | **【公】** 割合フラグはなし。`-ngl, --n-gpu-layers auto`、`--fit on` / `--fit-target 1024`、`-dev`（オフロード先） |
| バッチ | **【公】** `--max-num-batched-tokens`（Flash-Next 2048、MiniCPM5 512、bf16 参照 32768） | **【公】** `--max-num-batched-tokens 2048`（quickstart）／4096（最速起動）＝prefill チャンク | **【公】** `--prefill auto`（プロンプトのチャンク）と `--batch` スロット | **【公】** `--prefill 32768`（長文のプロンプト分割。1M コンテキストで効果が大きい） | **【公】** `-b, --batch-size 2048`、`-ub, --ubatch-size 512` |
| 既定のサンプリング | **【未】** README に既定値の明記なし（`generation_config` 依存）。Sly の起動例は T 1.0 / top_p 0.95 / top_k 20 | **【公】** `--override-generation-config '{"temperature":1.0,"top_p":0.95,"top_k":20,"min_p":0.0,"presence_penalty":0.0}'`。ベンチは T 0.7 / top_p 0.95 / top_k 20 | **【公】** 設定ファイルの `"sampling"`（例 T 1.0 / top_p 0.95 / top_k 20）。要求→共有 Chat 設定→`sampling`→モデルカードの順 | **【公】＋【源】** `reasoning_effort`（`MayaInstanceSettingsBody` の `Literal` から選択肢を読む）と `thinking_budget`（既定 32768）、`MAYA_GLM_THINK_RESERVE 1024`。ペナルティは `docs/SETTINGS.md` | **【公】** `--temp 0.80` / `--top-k 40` / `--top-p 0.95` / `--min-p 0.05` / `--repeat-penalty 1.00` |
| 思考の度合い | **【未】** エンジン側の選択肢は README に記載なし（チャットテンプレート側） | **【公】** `--default-chat-template-kwargs '{"enable_thinking": false}'` / `{"reasoning_effort":"medium"}` | **【公】** none / low / medium / high の 4 段 | **【公】＋【源】** `reasoning_effort`／`thinking_budget`（保存 API の引数名と同じ） | **【未】** 専用フラグなし。チャットテンプレートと要求側のオプションに依存 |

### 他製品の設定画面の並べ方（参考）

| 製品 | 並べ方の要点 | 出どころ |
|---|---|---|
| LM Studio | 「モデルを読み込む時」と「会話中」の 2 段。読み込み時は Context Length・GPU Offload（`max` / 比率 / `off`）・Flash Attention、会話中は Temperature / Top P / Min P / Repeat Penalty 系。`My Models` → ギア → モデルごとの既定（per-model defaults）として保存でき、`lms load --context-length --gpu` と同じ値を GUI から触れる | https://lmstudio.ai/docs/cli/local-models/load , https://lmstudio.ai/docs/app/advanced/per-model , https://lmstudio.ai/docs/typescript/api-reference/llm-load-model-config |
| Ollama | GUI というより `Modelfile` の `PARAMETER` と API の `options` に集約。`num_ctx`（既定 2048）・`num_predict`・`temperature`（0.8）・`top_k`（40）・`top_p`（0.9）・`min_p`・`repeat_penalty`（1.0）・`seed` と、投機は `draft_num_predict`。「モデルへ値を焼き込む」場所が 1 か所 | https://docs.ollama.com/modelfile |
| text-generation-webui | タブで役割を分ける: **Model** タブ＝モデルの選択・ローダー別の読み込み設定（llama.cpp なら `gpu_layers`・`ctx_size`・`cache_type`・`batch_size`・`ubatch_size`）と HF ダウンロード（repo id＋ファイル名を入力）、**Parameters** タブ＝生成パラメータ、**Chat** タブ＝会話。モデルの設定をドロップダウンに保存し、再選択時に復元する | https://github.com/oobabooga/text-generation-webui/wiki/04-%E2%80%90-Model-Tab , https://github.com/oobabooga/text-generation-webui/wiki/03-%E2%80%90-Parameters-Tab |

共通して言えること: **「どのモデルを・どれだけの文脈で読み込むか」を先に出し、速度・サンプリングの細かい値は後ろ／別の所へ置く**。
ControlDeck の ①〜⑥ の並び（下記）はこの並べ方と同じ方向で矛盾しない。

### 出どころ（すべて 2026-11-28 に閲覧）

- vLLM 公式 Engine Arguments: https://docs.vllm.ai/en/stable/configuration/engine_args/
- Sly-Radiance 公式 README（Tech stack / Options / Quickstart / Other checkpoints）: https://github.com/SlyBase/vllm-sly-radiance/blob/main/README.md
- Radiance 公式 README とツールリファレンス（GitHub ミラー。上流は Codeberg だがスクレイパー対策で本文が取得できなかったためミラーで確認）: https://github.com/AndrzejukPawel/radiance/blob/main/README.md , https://github.com/AndrzejukPawel/radiance/blob/main/docs/TOOLS.md , 上流 https://codeberg.org/StillDeadcode/radiance/src/branch/main/README.md
- Strata 公式 `docs/DETAILS.md`: https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md
- Maya 公式 `README.md` と `docs/SETTINGS.md` / `docs/MODELS.md`: https://github.com/mw00/project-maya/blob/main/README.md , https://github.com/mw00/project-maya/blob/main/docs/SETTINGS.md , https://github.com/mw00/project-maya/blob/main/docs/MODELS.md
- llama.cpp 公式 `tools/server/README.md`（CLI help と同じ内容）: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

## 目録候補（repo id とファイル名のみ・2026-11-28）

依頼の約束: **repo id とファイル名だけを書く**。大きさ・sha・ダウンロード URL の推測値は書かない。
下表の大きさはすべて `null`（未確認）で、取得の開始時に HF の API から読む（run3 の `size_gib=null` と同じ扱い）。
`gated` は HF API の `gated` フラグをそのまま転記した。**Web の閲覧だけ**で、重みの取得・実機での起動はしていない。
各ランタイムで動く形式だけを載せる（Strata は GSQ-RCO・Unsloth UD-*・Q8_0、llama.cpp は GGUF、
Sly は `sly_radiance_catalog.py` と同じ形式、Maya は Maya 量子化、Radiance は `.rad`）。
各表の「出典 URL」は記載済みの repo id から `https://huggingface.co/<repo id>` の形で機械的に導いたもの、
「確認日」はこの目録を Web で調べた日（2026-11-28）。外部取得は禁止したので新たな問い合わせはしていない。
公開 API の読み取りで分かったことだけを転記し、大きさ・sha・ダウンロード URL の値は推測して書かない。

### Sly-Radiance（safetensors: MXFP4 GPTQ / Quark AWQ / NVFP4 / ParoQuant ＋ W4A16 ドラフター）

| repo id | ファイル名 | 形式 | 無検閲 | gated | 大きさ | 出典 URL | 確認日 |
|---|---|---|---|---|---|---|---|
| `slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ` | `model-00001-of-00009.safetensors` 〜 `model-00009-of-00009.safetensors`、`model_mtp.safetensors` | MXFP4 GPTQ（compressed-tensors） | あり（heretic） | false | null | https://huggingface.co/slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ | 2026-11-28 |
| `amd/Qwen3.8-27B-Quark-AWQ-MXFP4` | `model.safetensors` | Quark AWQ MXFP4 | — | false | null | https://huggingface.co/amd/Qwen3.8-27B-Quark-AWQ-MXFP4 | 2026-11-28 |
| `unsloth/Qwen3.8-27B-NVFP4` | `model.safetensors`、`model_mtp.safetensors` | NVFP4（`RADIANCE_NVFP4_MXFP4=1` で MXFP4 経路） | — | false | null | https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4 | 2026-11-28 |
| `z-lab/Qwen3.8-27B-PARO` | `model.safetensors` | ParoQuant | — | false | null | https://huggingface.co/z-lab/Qwen3.8-27B-PARO | 2026-11-28 |
| `syvai/Qwen3.8-27B-DFlash2-W4A16` | `model.safetensors` | W4A16（drafter。上のモデルと併用） | — | false | null | https://huggingface.co/syvai/Qwen3.8-27B-DFlash2-W4A16 | 2026-11-28 |
| `akumaburn/Swift-1.5-Qwen3.8-27b-heretic` | `model-00001-of-00012.safetensors` 〜 `model-00012-of-00012.safetensors`、`model-mtp.safetensors` | 無検閲の元モデル（Sly の heretic 量子化の base_model） | あり（heretic） | false | null（このまま Sly で動くかは**未実測**） | https://huggingface.co/akumaburn/Swift-1.5-Qwen3.8-27b-heretic | 2026-11-28 |

### Radiance（`.rad` コンテナ）

| repo id | ファイル名 | 無検閲 | gated | 大きさ | 出典 URL | 確認日 |
|---|---|---|---|---|---|---|
| `StillDeadcode/qwen3.8-next-flash-fp8-iq4r-moe` | `qwen3.8-next-flash-fp8-iq4r-moe.rad` | — | false | null | https://huggingface.co/StillDeadcode/qwen3.8-next-flash-fp8-iq4r-moe | 2026-11-28 |
| `StillDeadcode/qwen3.8-27b-fp8` | `qwen3.8-27b-fp8.rad` | — | false | null | https://huggingface.co/StillDeadcode/qwen3.8-27b-fp8 | 2026-11-28 |
| `StillDeadcode/qwen3.6-35b-a3b-fp8` | `qwen3.6-35b-a3b-fp8.rad` | — | false | null | https://huggingface.co/StillDeadcode/qwen3.6-35b-a3b-fp8 | 2026-11-28 |
| `StillDeadcode/minicpm5-2b-fp8` | `minicpm5-2b-fp8.rad` | — | false | null | https://huggingface.co/StillDeadcode/minicpm5-2b-fp8 | 2026-11-28 |
| `StillDeadcode/minicpm5-2b-bf16` | `minicpm5-2b-bf16.rad` | — | false | null | https://huggingface.co/StillDeadcode/minicpm5-2b-bf16 | 2026-11-28 |

無検閲の `.rad` は公式 README に記載が無く、**未確認**（候補なし）。`rad-convert` で作る必要があるなら既存の手作業フロー扱い。

### Strata（GSQ-RCO / Unsloth UD-* / Q8_0）

| repo id | ファイル名 | 形式 | 無検閲 | gated | 大きさ | 出典 URL | 確認日 |
|---|---|---|---|---|---|---|---|
| `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` | `Q2_0/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf`（`00002` も）、`IQ2_XS/Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf`（`00002` も）、`IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf`（`00002` も）、`IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf`（`00002` も） | GSQ-RCO | — | false | null | https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF | 2026-11-28 |
| `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF` | `IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf`（`00002` も） | GSQ-RCO（Coder） | — | false | null | https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF | 2026-11-28 |
| `ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF` | `Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf`（`00002` も）、`Swift-Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-00001-of-00002.gguf`（`00002` も）、`Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_XXS-00001-of-00002.gguf`（`00002` も）、`Swift-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf`（`00002` も） | GSQ-RCO（Swift 1.5） | — | false | null | https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF | 2026-11-28 |
| `agentionai/Qwen3.8-Flash-Next-Gyro-GGUF` | `Qwen3.8-Flash-Next-Gyro-S-TQ1_0.gguf`（公式 README の例は revision 固定） | Gyro | — | false | null | https://huggingface.co/agentionai/Qwen3.8-Flash-Next-Gyro-GGUF | 2026-11-28 |
| `unsloth/Qwen3.8-Flash-Next-GGUF` | `UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf` 〜 `00004`、`Q8_0/Qwen3.8-Flash-Next-Q8_0-00001-of-00006.gguf` 〜 `00006` | Unsloth UD-* / Q8_0 | — | false | null | https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF | 2026-11-28 |
| `unsloth/Qwen3.8-27B-GGUF` | `Qwen3.8-27B-UD-Q4_K_XL.gguf`、`Qwen3.8-27B-Q8_0.gguf` | Unsloth UD-* / Q8_0 | — | false | null | https://huggingface.co/unsloth/Qwen3.8-27B-GGUF | 2026-11-28 |
| `unsloth/Qwen3.6-35B-A3B-GGUF` | `Qwen3.6-35B-A3B-Q8_0.gguf`、`Qwen3.6-35B-A3B-UD-IQ2_M.gguf` | Q8_0 / Unsloth UD-* | — | false | null | https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF | 2026-11-28 |
| `unsloth/DeepSeek-V4.1-Flash-1017-GGUF` | `UD-Q4_K_XL/DeepSeek-V4.1-Flash-1017-UD-Q4_K_XL-00001-of-00009.gguf` 〜 `00009` | Unsloth UD-* | — | false | null | https://huggingface.co/unsloth/DeepSeek-V4.1-Flash-1017-GGUF | 2026-11-28 |
| `OrcaRouter/Qwen3.8-Flash-Next-Uncensored-GGUF` | `Qwen3.8-Flash-Next-Uncensored-IQ3_XXS-00001-of-00002.gguf`（`00002` も）、`Qwen3.8-Flash-Next-Uncensored-IQ2_M-00001-of-00002.gguf`（`00002` も） | 汎用 GGUF（**既存の手作業変換 `iq_pack.py --compat-bf16` が要る＝既存 PLAN の印を維持**） | あり（Uncensored） | **true** | null | https://huggingface.co/OrcaRouter/Qwen3.8-Flash-Next-Uncensored-GGUF | 2026-11-28 |
| `SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF` | `IQ3_XXS/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_XXS-00001-of-00002.gguf`（`00002` も） | GSQ-RCO | あり（abliterated） | false | null | https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF | 2026-11-28 |
| `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | `Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_XXS.gguf`、`Huihui-Qwen3.8-27B-abliterated-Swift-1.5-GSQ-RCO-IQ3_XXS.gguf` | GSQ-RCO | あり（abliterated） | false | null | https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF | 2026-11-28 |
| `huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF` | `Strata-deepseek-v4.1-flash-IST-DAS/DeepSeek-V4.1-Flash-1017-GSQ-RCO-IQ2_XXS.gguf` | GSQ-RCO | あり（abliterated） | false | null | https://huggingface.co/huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF | 2026-11-28 |

### Maya（Maya 量子化）

| repo id | ファイル名 | Maya tier | 無検閲 | gated | 大きさ | 出典 URL | 確認日 |
|---|---|---|---|---|---|---|---|
| `peasantsmith/GLM-5.3-Flash-Maya-GGUF` | `Maya-S-v2-IQ2_XXS/GLM-5.3-Flash-Maya-S-v2-IQ2_XXS-00001-of-00003.gguf` 〜 `00003`、`Maya-S24/GLM-5.3-Flash-Maya-S24-IQ2_XXS_S-00001-of-00003.gguf` 〜 `00003`、`Maya-XS/GLM-5.3-Flash-Maya-XS-IQ1_S-00001-of-00002.gguf` 〜 `00002`、`Maya-M/GLM-5.3-Flash-Maya-M-IQ2_S-00001-of-00003.gguf` 〜 `00003`、`Maya-L/GLM-5.3-Flash-Maya-L-IQ3_S-00001-of-00004.gguf` 〜 `00004` | S / S24 / XS / M / L | — | false | null | https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF | 2026-11-28 |
| `peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF` | `GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-00001-of-00003.gguf` 〜 `00003` | Maya-M | あり（Derisked＝拒否を減らす重み編集・実験的） | false | null | https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF | 2026-11-28 |
| `peasantsmith/GLM-5.3-Maya-GGUF` | `Maya-XS/GLM-5.3-Maya-XS-00001-of-00004.gguf` 〜 `00004` | Maya-XS（744B） | — | false | null | https://huggingface.co/peasantsmith/GLM-5.3-Maya-GGUF | 2026-11-28 |
| `peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF` | `Maya-S/DeepSeek-V4.1-Flash-1017-Maya-S-IQ2_XXS-00001-of-00006.gguf` 〜 `00006`、`Maya-XS/DeepSeek-V4.1-Flash-1017-Maya-XS-v2-IQ1_S-00001-of-00003.gguf` 〜 `00003` | Maya-S / Maya-XS v2 | — | false | null | https://huggingface.co/peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF | 2026-11-28 |
| `peasantsmith/MiniMax-M3.1-Flash-Maya-GGUF` | `Maya-XS/MiniMax-M3.1-Flash-Maya-XS-IQ1_S-00001-of-00002.gguf` 〜 `00002`、`Maya-S/MiniMax-M3.1-Flash-Maya-S-IQ2_XXS-00001-of-00003.gguf` 〜 `00003` | Maya-XS / S | — | false | null | https://huggingface.co/peasantsmith/MiniMax-M3.1-Flash-Maya-GGUF | 2026-11-28 |
| `peasantsmith/Hy4-Flash-Maya-GGUF` | `Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-00001-of-00002.gguf` 〜 `00002`、`Maya-S/Hy4-Flash-Maya-S-IQ2_S-00001-of-00002.gguf` 〜 `00002` | Maya-XS / S | — | false | null | https://huggingface.co/peasantsmith/Hy4-Flash-Maya-GGUF | 2026-11-28 |
| `peasantsmith/Ling-3.2-air-Maya-GGUF` | `Ling-3.2-air-Maya-S-IQ2_XXS.gguf`、`Ling-3.2-air-Maya-M-IQ2_S.gguf` | Maya-S / M | — | false | null | https://huggingface.co/peasantsmith/Ling-3.2-air-Maya-GGUF | 2026-11-28 |
| `peasantsmith/Inkling-2-Mini-Maya-GGUF` | `Maya-L/Inkling-2-Mini-Maya-L-NVFP4.gguf` | Maya-L | — | false | null | https://huggingface.co/peasantsmith/Inkling-2-Mini-Maya-GGUF | 2026-11-28 |
| `orcarouter/DeepSeek-V4.1-Flash-1017-abliterated-GGUF` | `Mix-128G/DeepSeek-V4.1-Flash-1017-abliterated-Mix-128G-00001-of-00005.gguf` 〜 `00005`、`Mix-256G/DeepSeek-V4.1-Flash-1017-abliterated-Mix-256G-00001-of-00007.gguf` 〜 `00007` | abliterated（Maya 公式 README が載せる） | あり（abliterated） | **true**（HF 規約の承諾とトークンが要る／AMD は**未検証**） | null | https://huggingface.co/orcarouter/DeepSeek-V4.1-Flash-1017-abliterated-GGUF | 2026-11-28 |

### llama.cpp（GGUF）

| repo id | ファイル名 | 無検閲 | gated | 大きさ | 出典 URL | 確認日 |
|---|---|---|---|---|---|---|
| `unsloth/Qwen3.8-27B-GGUF` | `Qwen3.8-27B-Q8_0.gguf`、`Qwen3.8-27B-UD-Q4_K_XL.gguf` | — | false | null | https://huggingface.co/unsloth/Qwen3.8-27B-GGUF | 2026-11-28 |
| `ggml-org/GLM-4.7-Flash-GGUF` | `GLM-4.7-Flash-Q4_K.gguf`、`GLM-4.7-Flash-Q8_0.gguf` | — | false | null | https://huggingface.co/ggml-org/GLM-4.7-Flash-GGUF | 2026-11-28 |
| `ggml-org/gemma-4-E4B-it-GGUF` | `gemma-4-E4B-it-Q8_0.gguf`、`gemma-4-E4B-it-Q4_0.gguf` | — | false | null | https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF | 2026-11-28 |
| `google/gemma-4-12B-it-qat-q4_0-gguf` | `gemma-4-12b-it-qat-q4_0.gguf` | — | false | null | https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf | 2026-11-28 |
| `unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF` | `IQ4_NL/Llama-4-Scout-17B-16E-Instruct-IQ4_NL-00001-of-00002.gguf`（`00002` も） | — | false | null | https://huggingface.co/unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF | 2026-11-28 |
| `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | `Huihui-Qwen3.8-27B-abliterated-Q8_0.gguf` | あり（abliterated） | false | null | https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF | 2026-11-28 |
| `0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF` | `Qwen3.8-27B-Heretic-Q4_K_M.gguf`、`RVN-Q8_0.gguf` | あり（heretic / abliterated / uncensored） | false | null | https://huggingface.co/0bserverx/Qwen3.8-27B-Heretic-Abliterated-Uncensored-GGUF | 2026-11-28 |
| `huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF` | `UD-IQ1_M/GLM-5.3-Flash-UD-IQ1_M-00001-of-00003.gguf` 〜 `00003` | あり（abliterated） | false | null | https://huggingface.co/huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF | 2026-11-28 |
| `huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF` | `UD-IQ1_M/DeepSeek-V4.1-Flash-1017-UD-IQ1_M-00001-of-00005.gguf` 〜 `00005` | あり（abliterated） | false | null | https://huggingface.co/huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF | 2026-11-28 |
| `llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF` | `gemma-4-E4B-it-ultra-uncensored-heretic-Q4_K_M.gguf` | あり（ultra uncensored / heretic） | false | null | https://huggingface.co/llmfan46/gemma-4-E4B-it-ultra-uncensored-heretic-GGUF | 2026-11-28 |
| `AtomicChat/Inkling-2-Mini-Uncensored-GGUF` | `Inkling-2-Mini-Uncensored-AD-IQ3_S.gguf` | あり（Uncensored） | false | null | https://huggingface.co/AtomicChat/Inkling-2-Mini-Uncensored-GGUF | 2026-11-28 |

## 設定の統一: 共通の並び（2026-11-28 確定）

上の表を元に、指示書の **①モデル ②コンテキスト長 ③同時処理数 ④速さの調整 ⑤起動 ⑥エキスパート** の並びを確定する。
run3 の `GET /api/v1/models/settings/capabilities` の契約（①〜⑥の順、欄 `{id, label, type, choices?, min?, max?, step?, default, expert}`、
**ランタイムに無い欄は返さない**、id は保存 API の引数名と同じ）は維持したまま、欄の置き所を決める。

| 節 | 全ランタイムで揃える欄 | 既定（エキスパート切）で見せるもの | エキスパートで開くもの |
|---|---|---|---|
| ①モデル | 登録済みモデルを選択（目録からの取得・ファイルからの登録は別の部品） | 選択肢だけ。パスや repo id の自由入力は出さない | 保存先フォルダ・repo id・alias の詳細 |
| ②コンテキスト長 | 8K / 16K / 32K / 64K / 128K / 256K と「モデルの上限」。上限を超える値は選べない | 選択肢。上限・失敗の印は常時表示 | モデルの上限そのものの数値、rope scaling の実験値 |
| ③同時に処理する数 | 1 / 2 / 4 / 8 をランタイムの上限で絞って出す（Maya は 1〜4） | 選択肢 | スロット／バッチグループの内部設定 |
| ④速さの調整 | 概念的な選択だけ: 投機的デコード（なし／標準／強め）、思考の度合い（ランタイムにあるものだけ）、速さのプリセット | 主操作 1 個＋要対応の印のみ。細かい数値は出さない | — |
| ⑤起動 | 呼ばれたら起動／自動起動／アイドルで止めない | 3 択 | アイドル時間の数値 |
| ⑥エキスパート | ②〜④で隠した数値・引数・環境変数をすべてここへ。ランタイム固有の項目は原則ここ | 原則非表示 | 下の「ランタイム固有の置き所」を参照 |

### ランタイム固有の置き所

| ランタイム | ④ 速さの調整に出すもの | ⑥ エキスパートに置くもの | 出さないもの |
|---|---|---|---|
| Radiance | `--num-speculative-tokens`（3 / 0 相当の選択肢） | `--kv-cache-dtype`、`--host-pool-mib`、`--gpu-headroom-mib`、`--expert-vs-cache-ratio`、`--placement`、`--max-num-batched-tokens`、prefix-cache tiers、`--api-key` | ランタイムに無い `--gpu-memory-utilization` は出さない |
| Sly-Radiance | DFlash2 の深さ（3 / 5 / 7）、サンプリングのプリセット | `--gpu-memory-utilization`、`--max-num-batched-tokens`、`--kv-cache-dtype`、`--mamba-ssm-cache-dtype`、`--compilation-config.*`、`RADIANCE_*` 環境変数 | **prefix cache は欄として出さない**（利用者の決まりで常時オン。`false` は 422） |
| Strata | `--spec`（投機の深さ）と思考レベル（none / low / medium / high） | `--kv`、`--kv-streaming` / `--kv-resident` / `--kv-grow`、`--vram-reserve-mib`、`--resident-budget-gib`、`--pcie-frac`、`--prefill`、`--prompt-cache`、`"sampling"` ブロック、`--calibrate` の各値 | NVIDIA 限定の系列（`nvfp4`）はこの PC では出さない |
| Maya | `reasoning_effort`（保存 API の `Literal` から選択肢を読む）と thinking budget のプリセット | `--kv` / `MAYA_GLM_KV_INT8`、`--kv-streaming` / `--kv-resident`、`MAYA_GLM_RAM_HEADROOM_GB`、`MAYA_GLM_SPLIT`、`--prefill`、`--vram-reserve-mib`、`MAYA_GLM_NO_MTP` / `MAYA_GLM_SPEC_*` | モデルが決める `alias`（`setup_id` から Maya 自身が決める） |
| llama.cpp | `--spec-type`（なし / draft / ngram の選択）、`--temp` のプリセット | `-ctk` / `-ctv`、`--cache-prompt` / `--cache-reuse` / `--cache-ram`、`-ngl`、`--fit`、`-b` / `-ub` | ランタイムに無い「GPU メモリ割合」の欄は出さない（`--fit` と `-ngl` で代用） |

- **名前と選択肢をそろえる**: 同じ意味の欄は全ランタイムで同じ表示名にする（例「同時に処理する数」「KV キャッシュの形式」）。
  選択肢の上限・刻み（`step`）は保存 API の検証定数から読み、二重持ちしない（run3/run4 の契約どおり）。
- **削るより足す**: 設定が少ないランタイムは、上の表にある項目を ④ か ⑥ へ足して揃える。
  逆にランタイムに無い項目（例 llama.cpp の GPU メモリ割合、Radiance の prefix cache の専用スイッチ）は**出さない**。
- Sly の prefix cache 以外の「常時オン」項目も、切る選択肢を出さない場合は欄を作らず注記だけにする。
