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
| M2 | Maya の目録に GLM-5.3・DeepSeek の ds4-quant を足す。Maya の取得（setup で取る）を画面から | 未着手 |
| M3 | 取得の Job の自動再開（再起動の後も）。Strata・Maya・HF・Radiance で実測 | 実装済み（実機の再起動での確認待ち） |
| M4 | 3 つとも「フォルダを選ぶ → 候補のファイルの一覧 → 選ぶ」 | 未着手 |
| M5 | 設定の形を揃える（上の方針） | 未着手 |
| M6 | 無検閲: Strata の OrcaRouter の変換を Job で（取得 → `iq_pack.py` → run config）。Maya の Maya-M-Derisked・abliterated を目録に | 未着手 |

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
   - 載せる行は既存データだけ: Sly 5 件＋MiniCPM（未対応表示）、Strata は `strata_catalog` の系列×量子化（GSQ-RCO・Unsloth UD-*・Q8_0 を含む公式目録）、Maya は Derisked（`peasmith/GLM-5.3-Flash-Maya-M-Derisked-IQ2_S-GGUF`、repo id のみ・大きさ未確認）。llama.cpp は検索で選ぶ既存フローがあるので推奨行を置かない（確定した repo ができてから足す）。
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
