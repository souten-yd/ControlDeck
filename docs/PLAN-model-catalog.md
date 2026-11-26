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

M3 の載せ方（M2・M4 で Maya・Radiance の取得 Job を作るとき）: 取得の Job を `jobs.create(..., resume={JSON にできる平らな引数})` で作り、`app/models_mgmt/resume.py` と同じ形で `jobs.register_resumable(kind, factory, title=...)` をモジュールの読み込み時に登録する（factory は保存した引数から runner を作る）。秘密値（HF のトークン等）は resume に入れない。再開の記録・回数の歯止め（3 回・7 日）・停止時の扱いは Job 基盤が持つので、ランタイムごとに作らない。
