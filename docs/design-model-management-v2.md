# Model 機能 再設計（モデル管理 v2）詳細設計

最終更新: 2026-08-18

対象: Model 画面、llama.cpp、Ollama、SGLang、モデルライブラリ、think 設定、エンドポイント。
進捗と作業引き継ぎは `docs/HANDOVER-model-management-v2.md` を参照する。

## Context

Model 画面の設定階層が現状の使い方と噛み合っていない。

- **think が共通設定にある**: `RuntimePolicy.chat.reasoning`（off/auto/on）が全モデルへ一律に効く。思考の深さはモデルごとに変えたいのに、共通設定が個別設定を上書きする構造になっている（`chat_router._resolve_think`）。
- **ポートが 1 モデル 1 ポート固定**: `llama.save_instance` が port の一意性を強制するため、同じ endpoint（例 `:8090`）で GGUF を差し替えられない。OpenCode など外部クライアントは endpoint を直接叩くので、モデルを変えるたびに接続先の変更を強いられる。
- **一覧に順序の概念がない**: `instances` は dict の挿入順そのまま。自動起動の優先順位を制御できない。
- **設定の個別削除が UI から到達不能**: `LlamaRuntimePanel` の削除ボタンは `registrationOnly=false` の分岐にあるが、`ModelsPage` は `registrationOnly` でしか描画しない。`LlamaDetailSheet` も `onDelete` を渡していない。API (`POST /llama/instances/{alias}/delete`) は存在するのに押せない。
- モデル管理として当然あるべき機能（並べ替え・複製・GGUF 実体の削除・容量表示・HuggingFace からの直接取得）が無い。
- 評価用に **SGLang** を第三のランタイムとして扱えるようにしたい。

目標は、設定を「**環境 → 共通ポリシー → エンドポイント → モデル**」の 4 層に整理し、モデル固有の値をすべてモデル個別設定へ寄せること。`docs/design-model-runtime-assistant.md` §2 の 3 層設計に「エンドポイント」を追加した形になる。

---

## 現状の要点（実装前に把握しておくこと）

- **DB を使わない**。永続化は JSON 3 ファイル: `data_dir()/llama-runtime.json`、`ollama-settings.json`、`model-runtime-policy.json`。Alembic revision は不要な代わりに、**既定辞書（`llama.DEFAULT_INSTANCE` / `ollama.DEFAULT_SETTINGS` / `MODEL_CONFIG_KEYS`）へキーを足さないと保存が黙って捨てられる**（`save_instance` は `if key in DEFAULT_INSTANCE` でフィルタする）。
- llama.cpp は **systemd user unit** で常駐（`llama.unit_name` / `_unit_content` / `start_instance`）。Web プロセスの子にしない規約。`start_instance` は起動後 10 秒監視して即死を検知する。
- 実機 `llama-server` (b10001) は `--reasoning on|off|auto`、`--reasoning-budget N`（-1=無制限 / 0=即終了 / N>0=バジェット）、`-np/--parallel N`、`--kv-unified` を持つ（`--help` で確認済み）。
- **`-c/--ctx-size` は全スロットの合計**。`--parallel N` にすると 1 スロットあたり `ctx_size / n_parallel` になる（`chat_persist._context_max` のコメントが `/slots` の `n_ctx` を「parallel 分割後の実値」と明記）。
- `n_parallel` は既に保存され `--parallel` へ渡っているが、**UI に入力欄が無い**。
- HF からの GGUF 直ダウンロードは `role_presets.install` に既に実装がある（`.part` → `replace()` の原子的置換 + `job.set_progress`）。`huggingface_hub` 依存は無く httpx のみ。
- **ダウンロード経路にディスク空き容量チェックが無い**。既存の実装パターンは `files/archives.py:62 _ensure_free_space` のみ。
- 並べ替え UI の前例は `features/settings/MobileNavigationSettings.tsx:28-61` の ↑↓ ボタン方式（DnD ライブラリは未導入）。
- `RuntimePolicy.default_model_ref` は定義だけで**参照ゼロ**。

---

## A. エンドポイント概念の導入（ポート共有・優先度・自動起動）

「1 モデル = 1 ポート」をやめ、**エンドポイント（= 127.0.0.1 の待受ポート）を第一級の概念**にする。複数のモデル設定を 1 エンドポイントに束ね、そのエンドポイントでは常に 1 つだけが稼働する。

### データモデル（`backend/app/models_mgmt/llama.py`）

`llama-runtime.json` に `endpoints` を追加し、instance は `endpoint_id` で参照する。

```python
DEFAULT_ENDPOINT = {"id": "", "label": "", "port": 8080, "active_alias": ""}
MAX_ENDPOINTS = 8

DEFAULT_INSTANCE = {
    ...,                      # 既存キーはそのまま
    "endpoint_id": "",        # 新規。空なら port から解決
    "order": 0,               # 新規。1 始まり。小さいほど優先
    "think": "auto",          # 新規（B 参照）
    "think_budget_tokens": 0, # 新規（B 参照）
    "kv_unified": True,       # 新規（C 参照）
}
```

- `port` は instance からは**削除せず derived フィールドとして残す**。`list_instances()` / `runtime_status()` は従来どおり `port` と `base_url` を返すので、`providers.py` / `chat_persist.py` / OpenCode 連携の呼び出し側は壊れない。
- **移行**: `get_config()` の読込時、`endpoints` が空なら instance の distinct port ごとに `{"id": f"ep-{port}", "label": f"ポート {port}", "port": port}` を生成し `endpoint_id` を埋める。既存 port は一意なので 1:1 で無損失。`order` は既存の dict 挿入順で 1..N を付与。旧 `instance` 単一設定 → catalog 投影（llama.py:139-152）の後段に置く。

### 一意性ルールの置き換え

`save_instance` (llama.py:229-237) の port 重複エラーを撤廃し、代わりに:

- **endpoint 間**で port 一意（`save_endpoint` で検証）。加えて `ollama.base_url()` のポートと衝突したら 422。
- **alias** はカタログ内で一意（既存どおり）。
- **同一 model_path の重複禁止は緩和**する。同じ GGUF を別 CTX / 別量子化設定で持つのが複製機能（D-3）の目的なので、`endpoint_id` と `model_path` の組が同じときだけ拒否する。

### 同一エンドポイント内の排他起動

`start_instance(alias)` に、unit 書き出しの前段を追加:

```python
# 同一エンドポイントの他モデルを先に止める（port bind 競合を避ける）。
# 前例: llama.py:638-639 の legacy unit stop と同じ考え方。
for other in instances_on_endpoint(endpoint_id):
    if other["alias"] != alias and other["loaded"]:
        sd.stop(unit_name(other["alias"]))
```

起動成功後に `endpoints[ep]["active_alias"] = alias` を書き戻す。`stop_instance` は `active_alias` を消す。

### port → instance の逆引き（曖昧性の解消）

新関数 `resolve_instance_by_port(port) -> str | None` を追加し、次の優先順で 1 件に決める。

1. そのエンドポイントで実際に稼働中（`runtime_status` が RUNNING/STARTING）の instance
2. `endpoints[ep]["active_alias"]`（まだカタログに存在するなら）
3. そのエンドポイントの最優先 instance（`order` 昇順の先頭）

**置き換え対象**（すべて現在は `next((... if port == parsed.port))` で先頭一致を取っている）:

| ファイル | 関数 |
|---|---|
| `llama.py:738` | `ensure_ready_by_base_url` |
| `llama.py:769` | `mark_used_by_base_url` |
| `runtime_policy.py:118` | `model_output_tokens` |
| `runtime_policy.py:326` | `prepare_deep_research_context` |
| `chat_persist.py:74,95` | `_context_max` / `_prompt_tokens_probe`（集合判定なので `port in endpoint_ports()` へ） |

`providers.py:47-59` は `candidates` を base_url キーの dict で持つため、同一 endpoint の複数モデルが 1 エントリへ潰れる。**endpoint 単位に 1 provider エントリを作り、`models` にそのエンドポイントの全 alias を `order` 順で並べる**よう変更する。稼働中の alias に `loaded: true` を付ける。

### 順序（優先度）が効く範囲

`order` は「一般的なモデル管理」の慣習に沿って次に効かせる。

1. **一覧の表示順**: `list_instances()` を `(order, alias)` でソート。`provider_adapters.list_models` / providers の `models` も同順。
2. **自動起動の優先順位**: 同一エンドポイントで `auto_start` が複数あるとき、**最優先の 1 件だけ `systemctl --user enable`**、他は明示的に disable。`sync_instance_unit` を `_sync_endpoint_units(endpoint_id)` に一般化し、endpoint 内の全 unit の enable 状態を毎回まとめて同期する。
3. **オンデマンド起動のフォールバック**: `ensure_ready_by_base_url` が上記の逆引き 3 番目で最優先を選ぶ。
4. **既定モデルのフォールバック**: `RuntimePolicy.default_model_ref` が空 or 解決不能なとき、選択中ランタイムの `role == "llm"` 最優先 instance を既定にする（現在 未使用の `default_model_ref` をここで初めて実用化する）。

同時ロード上限（`provider_adapters._enforce_load_limit`）の**自動アンロードは行わない**（現状どおりエラー）。ただしエラーメッセージに「アンロード候補 = ロード中で最も優先度が低いモデル」を添える。

### API 追加 / 変更（`models_mgmt/router.py`）

```
GET    /models/llama/endpoints
POST   /models/llama/endpoints              {label, port}
PUT    /models/llama/endpoints/{id}         {label?, port?}
POST   /models/llama/endpoints/{id}/delete  # 所属 instance があれば 409
POST   /models/providers/{pid}/models/reorder  {order: [id, ...]}
```

`LlamaInstanceBody` に `endpoint_id: str | None`、`order: int | None (ge=1, le=64)` を追加。`port` は互換のため受理し続け、指定時は「その port の endpoint を探し、無ければ作る」。`_validated_provider_patch` の `forbidden` 集合に `endpoint_id` / `order` を追加（identity 系は専用ルートのみ）。

---

## B. think を共通設定から削除し、モデル個別の詳細設定にする

### 語彙

```python
# backend/app/models_mgmt/thinking.py（新規・両ランタイム共通）
THINK_MODES = ("auto", "off", "low", "medium", "high", "xhigh", "custom")
THINK_LEVEL_BUDGETS = {"off": 0, "low": 1024, "medium": 4096, "high": 16384, "xhigh": 32768}
```

- `auto` = モデル / チャットテンプレートの既定に任せる（何も送らない）
- `custom` = `think_budget_tokens`（1〜262144）を直接指定
- レベルは内部でバジェットへ写像するが、**UI ではレベルを選ぶとバジェット入力欄に対応値が入り、そのまま微調整できる**（プリセット扱い。既存 `PresetOrCustom` と同じ操作感）

### 共通設定からの削除

- `runtime_policy.ChatDefaults.reasoning` を**削除**（`timeout_seconds` は残す）。Pydantic v2 は既定で extra を無視するので、`reasoning` が残った既存 JSON もそのまま読める。
- `chat_router._resolve_think(mode, model)` を廃止し、`thinking.resolve(base_url, model) -> ThinkSpec` へ置換。呼び出し元は `chat_router.py:142`（WS ストリーム）、`chat_router.py:97`（`_llm`）、`chat_persist.py:410, 943`。
- `chat_persist.py:943` の `body.thinking or chat_defaults.reasoning` は `body.thinking` のみを尊重し、未指定ならモデル個別設定へ委ねる。

### 解決とランタイムへの伝達

`ThinkSpec { mode, budget_tokens }` を `RuntimeChatRequest` へ載せ（`thinking` / `disable_thinking` を置き換えつつ既存フィールドは互換のため残す）、`reasoning_effort` を新設。

| ランタイム | 伝達方法 |
|---|---|
| **llama.cpp** | instance の CLI 引数。`auto`→引数なし / `off`→`--reasoning off` / それ以外→`--reasoning on --reasoning-budget <budget>`。`detect_options()` の flags に `--reasoning` / `--reasoning-budget` がある時だけ付与する（`--flash-attn` / `--cpu-moe` と同じガード方式）。加えて既存どおりリクエスト毎に `chat_template_kwargs {"enable_thinking": bool}` を送る。 |
| **Ollama** | native `/api/chat` の `think`。`auto`→送らない / `off`→`False` / `low,medium,high`→同名 / `xhigh`→`"high"` / `custom`→`"high"`。バジェット非対応である旨を UI に注記する。 |
| **外部 OpenAI 互換** | `reasoning_effort` にレベルをそのまま送る（`custom` は最も近いレベルへ丸める）。`OpenAICompatibleRuntimeProvider._payload` に追加。 |
| **SGLang** | E 参照。`--reasoning-parser` + リクエストの `chat_template_kwargs` / `separate_reasoning`。 |

### 保存先

- llama.cpp: instance の `think` / `think_budget_tokens`（→ unit 引数なので**保存後に再起動が必要**。UI で明示する）
- Ollama: `MODEL_CONFIG_KEYS` に `think_budget_tokens` を追加。`think` は既存キーを流用。
- レガシー値の読み替え（`normalize_think` を拡張）: `""`/`"auto"`→`auto`、`"on"`→`high`、`"max"`→`xhigh`、`off/low/medium/high` はそのまま。

ワークフロー LLM ノードの `think` 選択肢（`frontend/src/features/workflows/nodeTypes.ts:624-634`）も同じ語彙へ揃える。

---

## C. 1 モデルの並列駆動（`--parallel`）

**可能**。llama.cpp の `-c` は全スロット合計なので、`ctx_size=262144, n_parallel=4` にすると **VRAM を増やさずに 64K × 4 並列**になる。これを UI で明示するのが要点。

- `LlamaInstanceControls` に「同時リクエスト数（スロット）」を追加（`n_parallel`）。API/保存は既に通っているのでフォーム追加のみ。**値はプリセットではなく自由入力**にする（`PresetOrCustom` を使わず素の数値入力、1〜64 を API の既存レンジ `ge=1, le=64` でそのまま検証）。CTX との割り算で意味が決まる値なので、丸めた候補を出すより直接指定できる方がよい。
- `kv_unified` を新設し `--kv-unified` / `--no-kv-unified` を出し分ける（`--kv-unified` が flags にある時だけ表示）。
- **フォームに実効値をライブ表示**: 「合計 CTX 262,144 → 1 リクエストあたり **65,536** × 4 並列」。`kv_unified` が有効なときは分割されない旨に切り替える。
- Ollama 側は `OLLAMA_NUM_PARALLEL`（サーバー環境変数）。`ollama.runtime_env()` に読み取りを追加し、`kv_cache_type` と同じく**表示のみ**（適用は `systemctl edit ollama` が必要で権限外）。

---

## D. 一般的なモデル管理機能

### D-1. 個別削除を到達可能にする（+ GGUF 実体の削除）

- 一覧行の削除ボタンを llama.cpp でも表示する（`Models.tsx:308` の `selectedProvider === "ollama" &&` ガードを外す）。`LlamaDetailSheet` に `onDelete` を渡す。
- `llama.delete_instance(alias, *, delete_file: bool = False)`。`delete_file` 時は (1) `files.resolve(model_path)` で許可ルート内を再検証、(2) **他 instance が同じ path を参照していないこと**を確認、してから `unlink`。`router.py:755` のハードコード `gguf_deleted: False` を実値にする。
- `POST /llama/instances/{alias}/delete` に `DeleteInstanceBody {delete_file: bool = False}` を追加。共通ルート `DELETE /models/providers/llama.cpp/models/{id}?delete_file=` も対応。
- 確認ダイアログ（既存 `ConfirmDialog` は `children` を取れる）に「GGUF ファイル本体も削除する（{サイズ}）」チェックを置く。既定 OFF。

### D-2. 並べ替え

`POST /models/providers/{pid}/models/reorder {order: [...]}` → `llama.reorder_instances(aliases)` / `ollama.reorder_models(names)`。
Ollama 側は `MODEL_CONFIG_KEYS` に `order` を足すが、`set_model_config` の「空/None/False はクリア」判定は `0` も真になる（`0 in (None, "", False)` が True）ので、**`order` は 1 始まりにし、クリア判定より前に専用分岐で処理する**。

UI は `MobileNavigationSettings.tsx:28-61` の `move(index, offset)` + ↑↓ ボタン（`h-11 w-11`、`aria-label` 付き）をそのまま踏襲する。DnD ライブラリは導入しない。

### D-3. モデル設定の複製

`llama.duplicate_instance(alias, new_alias, *, endpoint_id=None)`。設定を丸ごとコピーし、`auto_start=False`、`order` は元の直後、`endpoint_id` は既定で同じ（＝同一エンドポイントに 2 モデルが載る、A の主用途）。`POST /models/llama/instances/{alias}/duplicate`。UI は一覧行の `DropdownMenu` に「複製」。

### D-4. 容量表示

- `list_instances()` に `file_size_bytes` / `file_exists` を追加。
- `GET /models/llama/storage` → `{models_dir, total_bytes, free_bytes, items: [{path, size, used_by: [alias]}]}`。**どの instance からも参照されていない孤児 GGUF** も列挙し、そこから削除できるようにする。
- UI: 一覧行にサイズ、画面下部に「GGUF 合計 xx GB / 空き yy GB」。

### D-5. HuggingFace 直ダウンロード

新モジュール `backend/app/models_mgmt/hf.py`:

```python
async def search_models(q, limit=20) -> list[dict]          # ollama.hf_search を移設して共用
async def list_repo_files(repo, revision="main") -> list[dict]
    # GET https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=1
    # .gguf のみ抽出。 *-00001-of-0000N.gguf は 1 グループにまとめて総サイズを出す
async def download(job, repo, files, *, revision="main", register=None) -> dict
    # role_presets.install のダウンロード処理を一般化:
    #   .part へストリーム書き込み → replace() で原子的公開
    #   Range ヘッダでレジューム、job.set_progress(received, total)
    #   保存先 <選択したライブラリ>/{owner}--{name}/   ← F 参照
```

- **保存先ライブラリを選択式**にする（F）。既定は空きが最大のライブラリ（実機では NVMe `/data1tb/LLM`）。
- **開始前に空き容量を検証**する。`files/archives.py:62 _ensure_free_space` と同じ形の関数を置き、`tree` の `size` 合計 + 予備を、**選択したライブラリのファイルシステムに対して**要求する。
- gated repo 用に `Authorization: Bearer <token>` を送れるようにする。トークンは既存の暗号化ユーティリティで保存（`GET/PUT /models/hf/settings`）。未設定なら匿名アクセス。
- 完了後に `register` 指定があれば `llama.save_instance` で instance 登録（`role_presets.install:98-102` と同じ流れ）。**`role_presets.install` 自体をこの `hf.download` へ書き換えて重複を消す**。
- API: `GET /models/hf/search`、`GET /models/hf/repos/{repo:path}/files`、`POST /models/hf/download-jobs` → `jobs.create("model.hf_download", ...)`。job kind が `model.` 始まりなので既存の `useModelJobsStream` / `JobProgress` がそのまま進捗を拾う。
- UI: `PullSheet` の HF タブを「Ollama へ pull」と「GGUF を直接ダウンロード」の 2 系統に分ける。repo 検索 → **量子化バリアント一覧（ファイル名・サイズ・空き容量との比較）** → alias / エンドポイント / role を指定 → ジョブ開始。

---

## E. SGLang を評価用ランタイムとして追加

### 前提の確認結果（一次情報にあたって確定させたもの）

- **gfx1201 は SGLang の公式サポート対象**。ROCm の AI Ecosystem ドキュメントが Radeon の対応 arch として `gfx1201（RX 9070 series, AI PRO R9700/R9600D）` を明記しており、ROCm 7.14 のリリース記事も「SGLang support on AMD Radeon GPUs」を謳っている。**実機の Radeon AI PRO R9700 は対象内**。
- **公式の導入手段は Docker イメージ**（pip ではない）: `rocm/sgl-dev:v0.5.13.post1-ubuntu24.04-py3.14-rocm7.14`。
- ホスト要件は **amdgpu カーネルドライバ + コンテナランタイム**のみ。**ホストの ROCm は 7.14 でなくてよい**（コンテナが ROCm ユーザースペースを内包する）。実機は `/dev/kfd`・`/dev/dri/renderD128` があり、ユーザーは既に `video` / `render` グループに所属済み。ホスト ROCm 7.2.1 のままで条件を満たす。
- **Radeon では 2 つの環境変数が必須**: `SGLANG_USE_AITER=false`、`SGLANG_ROCM_FUSED_DECODE_MLA=false`。
- 公式注記の制限: 「一部のモデルは Radeon で正しく動作しない（特定の MoE モデル、Qwen3-ASR 等）」。
- SGLang は **safetensors 形式**を使うため GGUF 資産を llama.cpp と共有できない。モデルは別途取得が要る。

### 実機の残課題

| 項目 | 状態 |
|---|---|
| コンテナランタイム | **未導入**（docker / podman とも無し）。導入には sudo が要る |
| イメージサイズ | 同 repo の他タグは **23〜30GB（圧縮時）**、展開後はさらに大きい。※ドキュメント記載のタグは Docker Hub がサイズを公開していないため、これは同 repo の他タグからの推定 |
| 置き場所 | `/` は空き 29GB しかないが、**NVMe `/data1tb` に 775GB 空き**（F 参照）。コンテナストレージと weights は NVMe へ置けば足りる |
| モデル weights | safetensors を別途取得。27B クラスは FP8 でも十数 GB |

→ 容量は **F のストレージ整理で解決する**。コンテナストレージは rootless podman の `graphroot` を `$VOL/containers` に向ける（`~/.config/containers/storage.conf`。ユーザー設定ファイルなので sudo 不要）。weights も選択したボリューム配下のライブラリへ置く。残る前提は「コンテナランタイムの導入（sudo が要る・ユーザーが明示実行）」だけになる。

### 実装方針

**コンテナランタイムは Podman（rootless）を第一候補**にする。Docker のデーモン + `docker` グループは実質 root 相当の権限付与になり、「Web プロセスは一般ユーザーのまま」「非 root の systemd ユーザーサービス」という本プロジェクトの既存方針（`docs/design-model-runtime-assistant.md` の helper 設計、`control-deck-web` の user service 運用）と噛み合わない。rootless Podman なら既存の `video`/`render` グループ所属だけで `--device /dev/kfd --device /dev/dri` が通り、systemd **user** unit からそのまま起動できる。Docker が既にある環境では Docker も使えるよう、ランタイムは検出して両対応にする。

- **システム変更は `./deck.sh` へ**。`deck.sh sglang`（仮）で podman の導入とイメージ pull を行う。**Web プロセスからは sudo を要する操作を一切しない**。これは `enable-desktop` と同じ「システム変更はユーザーが明示実行」の流儀（メモリの運用ルール）。導入前に空き容量を検査し、足りなければ必要量を示して中止する。
- 新モジュール `backend/app/models_mgmt/sglang.py`。**`llama.py` と同形の API**（`is_installed / detect / list_instances / save_instance / start_instance / stop_instance / health / ensure_ready / unit_name / _unit_content`）にすることで、`provider_adapters` / `providers` / `runtime_provider` へは分岐 1 本ずつの追加で載る。
- 実行形態も llama.cpp と揃えて **systemd user unit**（`cdapp-sglang-<safe>-<hash>.service`、`Type=simple`、`Restart=on-failure`）。`ExecStart` は `podman run --rm` を 1 プロセスとして前景実行する。`ExecStopPost` でコンテナ名を明示削除。

```
ExecStart=/usr/bin/podman run --rm --name cdapp-sglang-<alias> \
  --device /dev/kfd --device /dev/dri \
  --ipc=host --security-opt seccomp=unconfined \
  -p 127.0.0.1:<endpoint.port>:<endpoint.port> \
  -v <models_dir>:/app/models:ro -e HF_HOME=/app/models \
  -e SGLANG_USE_AITER=false -e SGLANG_ROCM_FUSED_DECODE_MLA=false \
  -e GPU_MAX_HW_QUEUES=1 \
  rocm/sgl-dev:<tag> \
  python -m sglang.launch_server --host 0.0.0.0 --port <port> \
    --model-path /app/models/<...> --served-model-name <alias> ...
```

- ポート公開は **`127.0.0.1:` バインド固定**（`--network=host` は使わない）。llama.cpp と同じく外部公開しない。
- `GPU_MAX_HW_QUEUES=1` はコンテナにも渡す。llama.cpp で判明した RDNA4 のアイドル GPU 100% 問題（ROCm/ROCm#5706）は同じ GPU で起きるため。**実測で効いているか確認する**（SGLang は複数ストリームを前提にするため、性能とのトレードオフになる可能性がある）。
- **エンドポイント（A）は llama.cpp と共有**する。同一ポートに llama.cpp と SGLang を混在登録でき、排他起動はランタイムをまたいで効く。`resolve_instance_by_port` はランタイム横断で解決する。
- **自由引数は禁止**し、型付き設定だけを argv へ変換する（既存方針 `docs/design-llama-multi-instance.md`）。実在フラグの検出は `podman run --rm <image> python -m sglang.launch_server --help` を 1 回だけ実行してキャッシュする（`llama.detect_options()` 相当）。
- AMD GPU 電力プロファイル（`amd_gpu.preflight_argvs`）は llama.cpp の unit と同じく `ExecStartPre` で適用する。

### 設定のマッピング（llama.cpp でできることを SGLang でも）

| 概念 | llama.cpp | SGLang |
|---|---|---|
| コンテキスト長 | `--ctx-size` | `--context-length` |
| 並列（スロット） | `--parallel` | `--max-running-requests` |
| 出力上限 | `--n-predict` | リクエスト毎 `max_tokens` |
| KV 量子化 | `--cache-type-k/v` | `--kv-cache-dtype` |
| 量子化 | GGUF に内包 | `--quantization`（fp8 / awq / gptq / w8a8 等） |
| GPU オフロード | `--n-gpu-layers` | `--mem-fraction-static`（VRAM 占有率） |
| 投機的デコード | `--spec-type` / `--spec-draft-n-max` | `--speculative-algorithm`（EAGLE / NEXTN） + draft model |
| think | `--reasoning` / `--reasoning-budget` | `--reasoning-parser`（qwen3 / deepseek-r1 等） + リクエスト `chat_template_kwargs` |
| 一般設定 | — | `--chunked-prefill-size` / `--schedule-policy` / `--attention-backend` / `--disable-radix-cache` / `--enable-torch-compile` |

think（B）の `ThinkSpec` は SGLang でも同じ語彙で解決し、`reasoning_parser` 未設定なら「このモデルでは思考の分離ができない」旨を UI に出す。

---

## F. モデルライブラリ（NVMe を含む複数の保存先）

実機を調べたところ、**ストレージ構成が現在の実装と噛み合っていない**ことが分かった。

| マウント | デバイス | 容量 | 空き | 状況 |
|---|---|---|---|---|
| `/` | SATA SSD (TOSHIBA 240GB) | 218G | **29G (87%使用)** | OS + ホーム。`~/ドキュメント/LLM` に **36GB の GGUF**、`~/.local/share/control-deck/models` に 3.5GB |
| `/data1tb` | **NVMe (SanDisk 1TB)** | 916G | **775G (11%使用)** | `/data1tb/LLM` に **96GB のモデル**（Qwen3.6-27B / 35B-A3B / Qwen3.8-27B）があるが **ControlDeck からは見えない** |

原因は `config/config.yaml` の `files.allowed_roots` が `/home/souten` だけで、**`/data1tb` が許可ルート外**のため。`files.resolve()` を通る FilePicker・GGUF スキャン・instance 登録・HF ダウンロードのすべてが NVMe に触れない。結果として、空きが逼迫している `/` 側の `~/ドキュメント/LLM` だけが使われている（登録中の `llama` / `Qwen3.8-27B` はいずれもこちら）。

### 設計: ストレージを検出し、ユーザーが選ぶ

**マウント名（`/data1tb`）やデバイス名（`/dev/nvme0n1p1`）は環境ごとに違い、変わりうるのでコードにも設定にも直書きしない。** 実機のドライブを検出して一覧から選ばせる。

#### ボリューム検出（`libraries.detect_volumes()`）

`lsblk -J -o NAME,PATH,SIZE,TYPE,TRAN,ROTA,FSTYPE,UUID,MOUNTPOINT,MODEL -e7`（`/usr/bin/lsblk` は標準で存在。`-e7` で snap の loop を除外）を JSON で読み、`shutil.disk_usage()` で空き容量を足す。**新規依存は不要**。

```json
{ "uuid": "3ebc97b1-…", "device": "/dev/nvme0n1p1", "mountpoint": "/data1tb",
  "transport": "nvme", "model": "SanDisk SSD Plus 1TB A3N", "fstype": "ext4",
  "total_bytes": 983…, "free_bytes": 832…, "writable": true, "is_system": false }
```

- `type=part` かつ `fstype` がマウント済みのものだけを返す。`squashfs` / `loop` / `rom`、および CIFS 等のネットワークマウントは除外する。
- `writable` は実効ユーザーでの書き込み可否（`os.access(mountpoint, os.W_OK)`）。
- `is_system` はマウントポイントが `/` のもの。既定候補から外し、UI で警告する。

> **M.2 の物理スロット番号（M2_1 / M2_2 …）はソフトウェアからは取得できない。** 実機の `/sys/bus/pci/slots/` はスロット `0` (0000:0f:00) と `0-1` (0000:3f:00) しか公開しておらず、NVMe（0000:04:00.0）に対応する項目が無い。したがって UI では「スロット番号」ではなく **接続方式（NVMe / SATA）+ モデル名 + 容量 + 空き** で識別させる（`SanDisk SSD Plus 1TB A3N · NVMe · 916GB（空き 775GB）`）。これで実用上は一意に選べる。

#### ライブラリの持ち方

保存先を `data_dir()/models/gguf` 決め打ち（`role_presets._models_dir()`）にせず、**複数の「ライブラリ」を登録し、参照・ダウンロード・削除のすべてを任意のライブラリに対して行える**ようにする。

- `model-runtime-policy.json` に `model_libraries` を追加。**パスを直書きせず、ボリュームを UUID で参照して相対パスを持つ**（マウント名を変えても、デバイス番号が変わっても追従できる）:
  ```json
  "model_libraries": [
    {"id": "main", "label": "モデル", "volume_uuid": "3ebc97b1-…", "subpath": "LLM/gguf", "default": true},
    {"id": "builtin", "label": "内蔵", "volume_uuid": "", "subpath": "", "path": "~/.local/share/control-deck/models/gguf"}
  ]
  ```
  実効パス = `mountpoint(volume_uuid) + subpath`。**UUID が現在マウントされていなければ「未接続」として扱い**、`/` 側へ暗黙にフォールバックしない（マウント漏れ時に system ドライブを埋めてしまう事故を防ぐ）。`volume_uuid` が空のエントリは従来どおり絶対 `path` を使う（後方互換）。
- 保存時は解決後のパスを `files.resolve()` で検証する（許可ルート外は 422）。
- `model_libraries` 未設定なら従来の `data_dir()/models/gguf` 1 件を既定として自動生成する。
- `files.allowed_roots` にはユーザーが選んだボリュームのマウントポイントを追加する必要がある。**設定ファイルの変更なのでアプリからは書き換えず**、UI は「このボリュームを使うには `config.yaml` の `allowed_roots` に `<mountpoint>` を追加してください」と具体的な行を提示する（許可範囲の拡大をアプリが自己申告で行わない）。
- `GET /models/storage/volumes` が検出したボリューム一覧を返す（ライブラリ追加時の選択肢）。`GET /models/libraries` が各ライブラリの `{id, label, volume, path, mounted, total_bytes, free_bytes, gguf_count, gguf_bytes}` を返す。D-4 の容量表示はライブラリ単位に集約する。
- **参照**: 既存の `ollama.scan_gguf(dir)`（深さ 3・200 件上限・symlink スキップ）をライブラリ全体に適用し、`GET /models/libraries/{id}/scan` で GGUF 一覧を返す。「未登録の GGUF」を一覧して**ワンタップで instance 登録**できるようにする（`/data1tb/LLM` の 96GB がまさにこれに当たる）。
- **ダウンロード**: D-5 の HF ダウンロードは**保存先ライブラリを選択式**にし、既定を空きが最大のライブラリにする。開始前の空き容量検査（`archives._ensure_free_space` 相当）は選択したライブラリのファイルシステムに対して行う。
- **削除**: D-1 の GGUF 実体削除はライブラリ配下であることを `files.resolve()` で再検証したうえで行う。孤児 GGUF（どの instance からも参照されていないファイル）の一覧と削除もライブラリ単位で提供する。
- llama.cpp / SGLang どちらの instance も、任意のライブラリのファイルを `model_path` に取れる（現状どおり絶対パスで保持し、ライブラリはあくまで UI とスキャンの単位にする）。

---

## G. ControlDeck 本体と資産の NVMe 移設

**「NVMe 側の ControlDeck が初期起動するようにできるか」→ できる。** 条件は既に揃っている。

- `/data1tb` は `/etc/fstab` に UUID 指定・ext4・`defaults,noatime`・pass 2 で登録済み → **起動時に自動マウントされる**（手動/automount ではない）。
- `loginctl show-user souten` が `Linger=yes` → ログインなしで boot 時に user manager が起動する。
- ローカル fstab のマウントは `local-fs.target`（`sysinit.target` → `basic.target`）で完了し、**`user@1000.service` はその後に起動する**ので、user unit が動き出す時点で `/data1tb` は利用可能。
- `/data1tb` は `souten:souten 755` で書き込み可。
- `control-deck-web.service` は `deploy/systemd/control-deck-web.service.in` の `@REPO_ROOT@` を `deck.sh` の `install_web_unit()`（deck.sh:237-247）が置換して生成する。**パスは完全にパラメータ化されており、移設後に `./deck.sh service` を実行すれば追従する。**
- `data_dir` は `config.yaml` の `data_dir:` キーで差し替えられる（`backend/app/config.py:172, 199-202`。既定は `~/.local/share/control-deck`）。

ただし **`RequiresMountsFor=` は user unit では使えない**（user manager 自身の mount unit 名前空間に対して解決され、システム側のマウントは見えない）。マウント漏れ時に**気付かないまま `/` 側のマウントポイント配下へ新しい data_dir を作ってしまう**のが最悪のケースなので、テンプレートへ明示ガードを足す:

```
ExecStartPre=/usr/bin/findmnt --target @DATA_MOUNT@ --mountpoint @DATA_MOUNT@
```

失敗すれば `Restart=on-failure` / `RestartSec=3` で再試行され、ログに理由が残る。

### 目標レイアウト

**仮想環境・キャッシュを含め、肥大するものは一切 system ドライブ（240GB SATA）に置かない。**

移設先は F のボリューム検出で**選択**する。以下の `$VOL` は選んだボリュームのマウントポイントで、この実機では `/data1tb`（SanDisk 1TB NVMe, UUID `3ebc97b1-…`）。**マウント名もデバイス名も手順の中で変数として扱い、直書きしない。**

```
$VOL/
  ControlDeck/
    app/          リポジトリ + .venv + node_modules ← /home/souten/ControlDeck   (630MB)
    data/         data_dir                          ← ~/.local/share/control-deck (8.5GB)
    cache/        pip / uv / npm / playwright / HF  ← ~/.cache・~/.npm            (8.6GB)
  LLM/
    gguf/         GGUF 一元化      ← 既存 96GB + ~/ドキュメント/LLM              (36GB)
    ollama/       Ollama モデル    ← /usr/share/ollama/.ollama/models            (19GB)
  containers/     podman graphroot ← SGLang イメージ（E）                    (30〜60GB)
```

移設で書き込む設定（`config.yaml` の `data_dir` / `allowed_roots`、`environment.d`、`storage.conf`、systemd unit のマウントガード）はいずれも `$VOL` を展開した実パスになる。**ボリュームを後から差し替える場合はこれらを書き換える必要がある**ため、選定は移設前に確定させる。

### `/` に溜まっているもの（実測）

| 現在地 | サイズ | 移設先 |
|---|---|---|
| `~/ドキュメント/LLM` | **36G** | `$VOL/LLM/gguf` |
| `/usr/share/ollama/.ollama/models` | **19G** | `$VOL/LLM/ollama`（system サービス。sudo 要） |
| `~/.local/share/control-deck` | 8.5G | `$VOL/ControlDeck/data`（runtimes 3.7G / models 3.5G / features 758M / sdks 578M） |
| `~/.npm` | **5.9G** | `$VOL/ControlDeck/cache/npm` |
| `~/.cache/ms-playwright` | 1.9G | `$VOL/ControlDeck/cache/ms-playwright` |
| `~/ControlDeck`（`.venv` 354M + `node_modules` 218M 含む） | 630M | `$VOL/ControlDeck/app` |
| `~/.cache/pip` | 393M | `$VOL/ControlDeck/cache/pip` |
| `~/.cache/uv` | 392M | `$VOL/ControlDeck/cache/uv` |

合計 **約 73GB**。`/` の空きは **29GB → 約 100GB** になる。

`~/.cache/{pip,uv}` は消えても再生成されるだけなので必須ではないが、**環境変数を設定するついでにコストがゼロ**なので含める。逆に OS 側に残すものは次項で線引きする。

### `data_dir` の外にあるもの（見落としやすい）

`data_dir` を移すと **17 個のサブディレクトリが一括で付いてくる**（`runtimes`（llama.cpp + whisper.cpp ASR モデル）/ `models` / `features`（OpenCode の npm prefix 721M・pyinstaller）/ `sdks` / `trash` / `uploads` / `chat-uploads` / `project-lab` / `flow-apps` / `workflow-artifacts` / `plugins` / `scripts` / `icons` / `integrations` / `terminal-automation` / `rag` / `tmp` / `logs`）。**data_dir 配下は移設で自動的に解決する。**

問題は配下に**無い**もの。ただし**何でも NVMe へ移すわけではない**。判断基準を先に決める。

- **移す**: 上限なく肥大し続けるもの（モデル、ランタイム、コンテナイメージ、ビルドキャッシュ、data_dir）。GB 級になる or なりうるもの。
- **OS 側に残す**: パッケージ管理下のバイナリ（`gh` / `git` / `podman` など `/usr` 配下。apt が管理しており移すと更新で壊れる）、認証情報と設定（`~/.config/*`、`~/.config/gh` の GitHub 認証を含む）、数百 MB で頭打ちのもの。**移設の手間とリンクの複雑さに見合わないものは無理に動かさない。**

| 場所 | サイズ | 判断 |
|---|---|---|
| `/usr/share/ollama/.ollama/models` | **19G** | **移す**。`OLLAMA_MODELS`（sudo 要・後述） |
| `~/.local/share/opencode` | 433M | **移す**（`$VOL` へ移してシンボリックリンク）。セッション履歴で伸び続けるため |
| `~/ControlDeckApps`（**GitHub 管理のクローン先**） | 未作成 | **移す**。リポジトリが増えると読めない量になる。`config.yaml` の `git_apps_dir` で変更可能（`backend/app/config.py:174`、`gitrepos/service.py:26 repos_dir()`） |
| `repo/backups`（`deck.sh backup` の既定出力先） | — | リポジトリ移設で自動的に NVMe 側になる（`scripts/backup.sh:8` は引数でも指定可） |
| `~/.local/share/searxng`（src / venv / settings.yml） | 136M | **残してよい**。導入時に確定して以降ほぼ増えない。移したくなった時のために `SEARXNG_DIR` が既に効く（`scripts/setup-searxng.sh:17`）ことだけ控えておく |
| `~/.config/opencode` | 63M | **残す**。設定と認証。小さく、移すとリンク管理が増えるだけ |
| `~/.config/gh`（GitHub 認証）、`/usr/bin/{gh,git,podman}` | 小 | **残す**。apt / OS の管理下 |

**GitHub からの取得は 3 経路あり、うち 2 つは既に `data_dir` 配下**なので移設で自動解決する:

- **GitHub 管理（リポジトリのクローン）** → `git_apps_dir`（**data_dir 外**。上表のとおり `config.yaml` で変更する）
- llama.cpp のリリースバイナリ（`llama.py:396` の GitHub Releases API）→ `data_dir/runtimes/llama.cpp` ✓
- whisper.cpp の clone + ASR モデル（`asr.py:26`、モデル本体は HuggingFace から 1.6GB）→ `data_dir/runtimes/whisper.cpp/<version>` ✓

同様に、OpenCode / pyinstaller のアドオン導入（`features/registry.py` の npm prefix・専用 venv）も `data_dir/features` 配下なので追随する。

> OpenCode は `XDG_DATA_HOME` / `XDG_CONFIG_HOME` を使う。**これらを `environment.d` で全体に設定するのは避ける** — 他のすべてのアプリのデータ位置まで変わってしまい、影響範囲が読めない。データ側の 1 ディレクトリだけを移動してシンボリックリンクで元の場所に見せる方が安全で、切り戻しも容易。

ControlDeck の管理外で `/` を食っているもの（**移設対象にしない**。ユーザーが必要と判断したときだけ）: `~/ダウンロード` 7.8G、`~/llama.cpp`（手動 clone）1.3G、`~/.cache/google-chrome` 1.1G。

### 環境変数で「戻ってこない」ようにする

移すだけでは次回の `pip install` / `npm install` / Playwright 導入で `/` に再生成される。**格納先を環境変数で固定する。**

`~/.config/environment.d/control-deck.conf`（**systemd user manager が起動時に読む**ので、`control-deck-web` と全 `cdapp-llama-*` unit に効く。sudo 不要）:

```
PIP_CACHE_DIR=$VOL/ControlDeck/cache/pip
UV_CACHE_DIR=$VOL/ControlDeck/cache/uv
npm_config_cache=$VOL/ControlDeck/cache/npm
PLAYWRIGHT_BROWSERS_PATH=$VOL/ControlDeck/cache/ms-playwright
HF_HOME=$VOL/ControlDeck/cache/huggingface
```

SearXNG は上の判断どおり `/` に残すので `SEARXNG_DIR` は設定しない（後で移したくなった時にこの変数が効く、という事実だけ控えておく）。

対話シェル（`./deck.sh` を手で叩く経路）にも効かせるため、同じ内容を `~/.profile` にも入れる。`environment.d` はログインシェルには適用されないため両方要る。

- **podman**: `~/.config/containers/storage.conf` の `graphroot`（後述）。sudo 不要。
- **Ollama**: system サービスなので `sudo systemctl edit ollama` で `Environment=OLLAMA_MODELS=$VOL/LLM/ollama` を追加し、ディレクトリを `ollama:ollama` 所有にする。**sudo が要るのでユーザーの明示実行**。移すまでは 19GB が `/` に残る。

### 絶対パスを埋め込んでいる箇所と対処

移設で壊れるのはここだけ。いずれも再生成の口が既にある。

| 対象 | 内容 | 対処 |
|---|---|---|
| `control-deck-web.service` | `WorkingDirectory` / `EnvironmentFile` / `ExecStartPre` / `ExecStart` | `./deck.sh service` で再生成（テンプレート置換） |
| `config.yaml` | `data_dir`（新規追加）、`files.allowed_roots`、`application_builder.dotnet_path`（**現在 data_dir 配下を指している**） | 手で更新。移設ツールが書き換える |
| `.venv` | `pyvenv.cfg` と shebang に旧パスを内包 | **移動せず作り直す**。`deck.sh` の `ensure_venv()` が `requirements.txt` の SHA-256 スタンプで自動再構築する |
| `runtimes/llama.cpp/current` | 旧 data_dir 配下への絶対 symlink | `llama.switch_backend()` で張り直す（移設ツールから呼ぶ） |
| `cdapp-llama-*.service` | `ExecStart` にサーバーバイナリ・モデル・ログの絶対パス | `sync_instance_unit()` / `start_instance()` が毎回書き直すので、移設後に各 instance を保存すれば再生成 |
| `llama-runtime.json` | `instances[].model_path`（`~/ドキュメント/LLM/...`） | 移設ツールが新パスへ書き換え |

DB 内のアプリ登録パス（`~/cd-sample-app`）と `git_apps_dir`（`~/ControlDeckApps`）は移動対象外なので影響しない。

### 移設の実施手順（一度きりの作業。私が実行する）

**移設は `deck.sh` のサブコマンドにも UI にもしない。** 一度きりの作業であり、恒久機能として持つ価値がないため。恒久的に残すのは `config.yaml`・`environment.d`・`storage.conf` の設定変更だけ。

1. `./deck.sh backup`（SQLite の整合バックアップ。既存実装）を取り、退避先を控える
2. `systemctl --user stop control-deck-web` と全 `cdapp-llama-*` を停止
3. `mkdir -p $VOL/ControlDeck/{app,data,cache} $VOL/LLM/gguf $VOL/containers`
4. `rsync -aH --info=progress2` で移送（**`rsync` 完了・検証まで旧側は消さない**）
   - data_dir → `data/`
   - リポジトリ → `app/`（`.venv` と `node_modules` は除外。作り直す方が安全）
   - `~/ドキュメント/LLM/*.gguf` と既存 `$VOL/LLM/*` → `LLM/gguf/`
   - `~/.npm`・`~/.cache/{pip,uv,ms-playwright}` → `cache/`
   - `~/.local/share/opencode` → `$VOL/ControlDeck/opencode`（移送後に元の場所へシンボリックリンク）。`~/.config/opencode` と `~/.local/share/searxng` は **`/` に残す**
5. `config.yaml` を更新: `data_dir: $VOL/ControlDeck/data`、`files.allowed_roots` に `$VOL` を追加、`git_apps_dir: $VOL/ControlDeck/apps`、`application_builder.dotnet_path` を新 data_dir 配下へ
6. `~/.config/environment.d/control-deck.conf` と `~/.profile` に環境変数を追加 → `systemctl --user daemon-reload`
7. `llama-runtime.json` の `instances[].model_path` を新パスへ書き換え
8. 新パスで `./deck.sh service` — `ensure_venv()` が `.venv` を再構築し、`install_web_unit()` が unit を再生成する
9. `runtimes/llama.cpp/current` symlink を張り直す（`llama.switch_backend()` 相当）→ 各 llama instance を保存して `cdapp-llama-*.service` を再生成
10. 起動・疎通・RAG（embedding / reranker）・OpenCode・SearXNG 検索・GitHub 管理のクローンを確認
11. **確認が取れてから**旧ディレクトリを削除する。ここは私が勝手に消さず、削除コマンドを提示して確認を取る

> リポジトリ自身を移動するため、旧パスの `deck.sh` を実行したまま自分を消す形にはしない。手順 4 は rsync（コピー）で行い、旧側の削除は最後の 11 に分離する。

### SGLang のコンテナストレージ

rootless podman の格納先を `~/.config/containers/storage.conf` で NVMe へ向ける。**ユーザー設定ファイルなので sudo は不要**。

```toml
[storage]
driver = "overlay"
graphroot = "$VOL/containers/storage"
runroot = "/run/user/1000/containers"
```

これで 23〜30GB のイメージも `/` を圧迫しない。E の唯一残る前提は「podman 本体の導入（sudo）」だけになる。

---

## UI/UX 設計

情報の階層を「**環境 → 共通ポリシー → エンドポイント → モデル**」に統一する。同じ設定を 2 か所に出さない。

### Models 画面（一覧）

各行を「優先度つきカード」にする。並べ替え・状態・容量・操作が 1 行で完結する。

```
┌────────────────────────────────────────────────────────┐
│ ↑ ↓ │ ● Qwen3.8-27B                   [稼働中]  ⋮      │
│  1  │   llama.cpp · :8090 · 15.2GB · CTX 256K → 64K×4  │
│     │   💭 high (16,384)          [アンロード]         │
├────────────────────────────────────────────────────────┤
│ ↑ ↓ │ ○ Qwen3-Coder-30B              [同じ :8090]  ⋮   │
│  2  │   llama.cpp · :8090 · 17.8GB · CTX 128K          │
│     │   💭 auto                        [ロード]        │
└────────────────────────────────────────────────────────┘
   GGUF 合計 33.0GB · 空き 29.0GB          [+ モデルを追加]
```

- **↑↓ ボタン**（44px タップ領域・`aria-label`）で優先度を変更。既存 `MobileNavigationSettings` の `move()` を踏襲。
- **同じエンドポイントを共有する行は視覚的にグルーピング**し（左端のポート帯 + 「同じ :8090」バッジ）、「ロード」を押すと同居モデルが停止することをその場で説明する。ロード前に「`Qwen3.8-27B` を停止して切り替えます」という確認を出す。
- `⋮` メニュー（既存 `DropdownMenu`）: 詳細設定 / 複製 / エンドポイントを変更 / 削除。
- 一覧の主要指標（サイズ・CTX・並列・think）を副題に出し、詳細を開かなくても現状が読めるようにする。

### モデル個別設定シート

段階を 3 つに固定し、上に行くほど使用頻度が高い並びにする（`docs/design-model-runtime-assistant.md` §2 第 3 層に準拠）。

1. **よく使う**: エンドポイント / CTX / 同時リクエスト数 / 出力上限 / **思考** / 常駐（idle 除外）
2. **メモリ・速度**: GPU オフロード / Flash Attention / K/V 量子化 / batch・ubatch / KV unified
3. **上級**: sampling / threads / mmap・mlock / MTP・MoE / mmproj

- **思考ブロック**（両ランタイム共通コンポーネント `ThinkingControl`）:
  セグメント選択 `自動 | オフ | 低 | 中 | 高 | 最高 | カスタム` → 選ぶとバジェット入力に対応値（1,024 / 4,096 / 16,384 / 32,768）が入り、そのまま微調整できる。`自動`/`オフ` ではバジェット欄を隠す。Ollama 選択時は「バジェット非対応（レベルのみ反映）」と注記。
- **CTX と並列の関係を必ず可視化**する。`CTX 262,144 ÷ 4 並列 = 1 リクエストあたり 65,536` をフォーム直下にライブ表示し、「256K を 1 本で使う」か「64K を 4 本で使う」かを VRAM を増やさず選べることが伝わるようにする。
- llama.cpp / SGLang は **unit 引数なので保存だけでは反映されない**。変更があるフィールドに「再起動で反映」ピルを出し、ボタンを `保存` / `保存して再起動` の 2 択にする。

### 共通設定シート

`チャット思考` の select を**削除**し、跡地に「思考の設定は各モデルの個別設定へ移動しました」という 1 行の導線（モデル一覧へ戻るリンク）を一時的に置く。

### エンドポイント管理

共通設定シート内に「エンドポイント」セクションを新設。`:8090 メイン（2 モデル・稼働: Qwen3.8-27B）` のような行 + 追加/編集/削除。外部クライアント向けに `http://127.0.0.1:8090/v1` をコピーできるボタンを置く（OpenCode の接続先固定が本機能の主目的）。

### モデルライブラリ

共通設定シートに「モデルの保存場所」セクションを置く。各ライブラリを `NVMe (1TB) · /data1tb/LLM · 96GB / 空き 775GB` のように容量バー付きで並べ、追加・編集・既定の切替ができる。空きが少ないライブラリには警告色を出す。

同じ画面に **「未登録の GGUF」** を出す。`/data1tb/LLM` の 3 モデルのように、ディスクにはあるが instance 化されていないファイルを一覧し、`+ 登録` でエンドポイント / alias を決めてワンタップ登録できるようにする。**どの instance からも参照されていない孤児 GGUF** も同じ一覧でサイズ付きに示し、そこから削除できる。

### HuggingFace ダウンロード

`repo 検索 → 量子化バリアント選択 → 配置先ライブラリ` の 3 ステップ。バリアント一覧では **ファイルサイズと選択中ライブラリの空き容量を並べて表示**し、入らないものは選べないようにする。既定の配置先は空きが最大のライブラリ（実機では NVMe）にして、`/` を埋めてしまう事故を既定で避ける。進捗は既存のジョブカードに出るので、シートを閉じても継続する。

### レスポンシブ

既存の流儀どおり 1280 / 390 / 320px を確認する。320px では ↑↓ とカード副題が折り返しても崩れないこと、`⋮` メニューが画面外に出ないことを見る。

---

## ファイル構成

### バックエンド（`backend/app/models_mgmt/`）

| ファイル | 変更 |
|---|---|
| `llama.py` | endpoints / order / think / kv_unified、排他起動、`resolve_instance_by_port`、`duplicate_instance`、`delete_instance(delete_file=)`、`reorder_instances` |
| `ollama.py` | `MODEL_CONFIG_KEYS` に `think_budget_tokens` / `order`、`normalize_think` 拡張、`reorder_models`、`runtime_env` に `OLLAMA_NUM_PARALLEL` |
| `runtime_policy.py` | `ChatDefaults.reasoning` 削除、`default_model_ref` の解決を実装、port 逆引きを `resolve_instance_by_port` へ |
| `runtime_provider.py` | `RuntimeChatRequest` に `ThinkSpec` / `reasoning_effort`、SGLang provider |
| `provider_adapters.py` | order 順の一覧、SGLang 分岐、上限エラーへの候補提示 |
| `providers.py` | endpoint 単位のグルーピング |
| `router.py` | endpoints / reorder / duplicate / storage / hf / sglang の各ルート |
| `thinking.py` | **新規**。think 語彙・写像・解決 |
| `libraries.py` | **新規**。モデルライブラリの登録・スキャン・容量・孤児検出（F） |
| `hf.py` | **新規**。検索 / ファイル一覧 / ダウンロード（`role_presets` から移設） |
| `sglang.py` | **新規**。llama.py と同形のランタイム管理 |
| `role_presets.py` | ダウンロード処理を `hf.download` へ委譲、保存先をライブラリ経由に |

`backend/app/workflows/chat_router.py` / `chat_persist.py` は `_resolve_think` 廃止に伴う追従。

### フロントエンド

`Models.tsx` は現在 1433 行で、今回の追加でさらに肥大する。**`frontend/src/features/models/` へ分割**する（`features/` 配下に機能別ディレクトリを置くのが本プロジェクトの慣習）。

```
frontend/src/features/models/
  ModelList.tsx           一覧・並べ替え・エンドポイントのグルーピング
  ModelCard.tsx           1 行のカード（状態・容量・think・メニュー）
  ThinkingControl.tsx     両ランタイム共通の think UI
  LlamaInstanceForm.tsx   llama.cpp 個別設定（CTX×並列の実効値表示）
  SglangInstanceForm.tsx  SGLang 個別設定
  OllamaModelConfig.tsx   Ollama 個別設定
  EndpointManager.tsx     エンドポイント CRUD
  HuggingFaceDownload.tsx repo 検索 → バリアント → 配置
  CommonSettingsSheet.tsx 共通設定（think を削除）
  shared.tsx              L / Toggle / PresetOrCustom / プリセット定数（現 Models.tsx から移設）
frontend/src/api/models.ts  api() ラッパ（api/flowApp.ts・projectLab.ts が雛形）
```

`pages/Models.tsx` はタブ切替と各パネルの組み立てだけを残す。

---

## 実装順序

0. **G: NVMe 移設**（コード変更ではなく一度きりの作業 + 設定変更）。`/` の空きを 29GB → 約 100GB にし、以降の作業（特に E のコンテナイメージと D-5 のダウンロード）の前提を作る。コードへの恒久的な追加は `control-deck-web.service.in` へのマウントガード 1 行だけ。
1. **F: モデルライブラリ**（ボリューム検出 + `allowed_roots` へ選択ボリューム追加 + `model_libraries` + スキャン/容量 API）。NVMe 上のモデルが見えるようになり、以降のダウンロード・削除の置き場所が決まる。
2. **A: エンドポイント基盤**（llama.py のデータモデル + 移行 + 排他起動 + `resolve_instance_by_port` + 呼び出し側の置換）。ここが他すべての土台。
3. **B: think のモデル個別化**（`thinking.py` + 共通設定からの削除 + 各 provider への伝達）。
4. **D-1/D-2/D-3: 削除・並べ替え・複製**（API + UI 到達性の修正）。
5. **C: 並列駆動の UI 露出**（`n_parallel` 自由入力 / `kv_unified` / 実効 CTX 表示）。
6. **フロントエンド分割**（`features/models/`）と一覧カードの作り直し。ここまでで「使える」状態にする。
7. **D-4/D-5: 容量表示と HuggingFace ダウンロード**（F のライブラリ上に載る）。
8. **E: SGLang**（podman 導入 → `sglang.py` → provider 統合 → UI）。前提（コンテナランタイム導入）がユーザー操作を要するため最後に置き、未導入でも 0〜7 が無傷であることを確認する。

`AGENTS.md` / メモリの運用ルールに従い、機能単位でブランチ → PR → マージ。0 は作業＋設定変更なので PR は小さい。2〜8 は 4 本程度に分けるのが妥当（2+3 / 4+5+6 / 7 / 8）。

---

## 検証

### 自動テスト（`./deck.sh test`）

- `backend/tests/test_llama_runtime.py`: **port 一意の既存テストを置換**する。現行の `test_multi_instance_catalog_uniqueness_and_unit_names:71-72`（`pytest.raises(ValueError, match="port 8080")`）と API 側 `:199-201`（422 + `"port 8202"`）が旧契約を固定しているため、必ず更新が要る。新規: 同一エンドポイントへの 2 モデル登録、排他起動、`resolve_instance_by_port` の 3 段階優先、`order` による auto_start enable の選定、旧 JSON からの endpoints 移行、duplicate、`delete_file`（他 instance が参照中なら拒否）。
- 新規 `test_model_thinking.py`: `auto/off/level/custom` × llama argv / Ollama think 値 / OpenAI `reasoning_effort` の解決。レガシー値（`on` / `max` / `""`）の読み替え。
- 新規 `test_model_libraries.py`: ライブラリ登録が `files.resolve()` で許可ルート外を拒否すること、複数ライブラリ横断のスキャン、孤児 GGUF の検出（参照中は孤児にしない）、容量集計、`model_libraries` 未設定時の既定 1 件へのフォールバック。
- 新規 `test_hf_download.py`: tree API のパース（分割 GGUF のグルーピング）、`.part` → `replace()`、**選択ライブラリのファイルシステムに対する**空き容量不足での事前エラー、Range レジューム。
- `test_runtime_policy.py`: `chat.reasoning` を含む旧 JSON がエラーなく読めること、`default_model_ref` 未設定時の最優先モデルへのフォールバック。
- `test_llm_providers.py`: endpoint グルーピング後の provider カタログ形状、order 順の `list_models`。

### E2E（`frontend/e2e/`）

`llama-runtime-settings.spec.ts` は書き込みキー（`:48-50`）とダイアログ名 `"${alias} · モデル個別設定"` に依存し、`model-provider-common.spec.ts` は `getByRole("listitem").first().getByRole("button").first()` で一覧の DOM 構造に依存する。**カード構造の変更に合わせて両方更新する**。新規: 並べ替え（↑↓ で順序が入れ替わり再読込後も保持）、削除確認、複製、think のレベル→バジェット連動、HF ダウンロード開始。

### 実機確認

0. **移設後（G）**: 一度 **再起動して**、選択ボリュームのマウント後に `control-deck-web` が自動起動すること、`/` の空きが増えていること、旧パスへ何も書き戻っていないこと（`pip` / `npm` / Playwright を 1 回ずつ走らせて `~/.cache` が育たないこと）を確認する。
1. `./deck.sh` で起動 → `systemctl --user restart control-deck-web`（**ユーザーサービスなので sudo は使わない**）。
2. 既存の `llama-runtime.json`（`llama`:8090 / `Qwen3.8-27B`:8091 / `embed-bge-m3`:8094 / `rerank-qwen3-4b`:8095）が endpoints へ移行され、稼働状態と RAG が壊れないこと。**移行前に JSON をバックアップする**。
3. `Qwen3.8-27B` を `:8090` へ移し、既存 `llama` と同居させる。片方をロード → もう片方をロード → 自動で切り替わり `http://127.0.0.1:8090/v1` の endpoint が変わらないことを OpenCode から確認。
4. **CTX 262,144 / 並列 4** で起動し、`GET http://127.0.0.1:8090/slots` の `n_ctx` が 65,536 になること、4 本の同時リクエストが捌けることを確認。
5. think を `high` にして起動 → unit の `ExecStart` に `--reasoning on --reasoning-budget 16384` が入ること、チャットで思考が打ち切られること。`off` で思考が出ないこと。
6. **NVMe ライブラリ**: `config.yaml` に選択ボリュームを追加 → Model 画面に既存の 3 モデルが「未登録の GGUF」として出ること。うち 1 つを登録して起動できること。
7. HF から小さい GGUF（1GB 未満）を **NVMe ライブラリ指定で**ダウンロードして自動登録 → 起動。空き容量を超えるファイルを選ぶと開始前に止まること。
8. GGUF 実体の削除が NVMe 上のファイルに対しても効き、他 instance が参照中なら拒否されること。
9. UI を 1280 / 390 / 320px で確認（Playwright は `.venv` に導入済み）。

---

## 今回スコープ外（提示のみ・後続の候補）

いずれも「一般的なモデル管理機能」として妥当だが、上記だけで十分に大きいため分離する。

- **GGUF メタデータの read-only パーサ**: ヘッダだけを上限付きで読み、architecture / 量子化 / 学習時 context / MoE expert / MTP 層を取得して、**対応する詳細設定だけを出す**（`docs/design-model-runtime-assistant.md:101` に設計だけあり未実装）。MTP を持たないモデルで `draft-mtp` を選んで起動失敗する現在の事故を根本から防げる。
- **VRAM 見積もりと事前フィット判定**: モデルサイズ + KV（ctx × 層 × cache type）から所要 VRAM を概算し、ロード前に警告する。
- **ベンチマーク**: モデルごとに tok/s・初回 token 時間を計測して記録し、一覧で比較する。SGLang 評価そのものにも要る。
- **設定のインポート / エクスポート**（JSON）、タグ・お気に入り・検索フィルタ。
- **設定の重複整理**: `ollama-settings.json` の `idle_unload_enabled` / `idle_unload_minutes` は UI から書けるのに実際は `RuntimePolicy` が正で、効かない二重設定になっている。
- **`save_instance` が role=llm のとき常に `selected_alias` を奪う問題**（`llama.py:248`）。既存の別モデルを選択中でも、他モデルを保存しただけで既定チャット先が切り替わる。A の実装中に直せるなら直す（新規作成時と明示 select 時のみに限定）。

## リスク

- **SGLang の前提はコンテナランタイムの導入（sudo が要る）**。gfx1201 は公式サポート対象で容量も NVMe で足りるため、当初懸念していたブロッカーは解消したが、podman/docker の導入だけはユーザーの明示実行が要る。E は「導入を試せる状態にする」までを成果とし、未導入・動作不良でも他機能へ影響しない構成を受入条件にする。Radeon では一部の MoE モデルが動かない旨が公式に注記されている点も、評価時の判断材料として UI に出す。
- **`allowed_roots` への `/data1tb` 追加は許可範囲の拡大**にあたる。`files.resolve()` の拒否リスト（`~/.ssh` 等）はホーム基準なので、NVMe 側に機微なデータが無いことを確認したうえで、ライブラリとして使うディレクトリ（`$VOL/LLM`）に絞って許可することも検討する。移設後は `$VOL/ControlDeck/data` に DB と鍵が載るため、**そこは許可ルートに含めない**（現在ホーム配下の data_dir が拒否リストで守られているのと同じ扱いを移植する必要がある）。
- **移設は稼働中の資産を動かす**。バックアップ取得 → コピー → 検証 → 旧削除 の順を厳守し、旧ディレクトリの削除は確認を取ってから行う。特に `/usr/share/ollama` は system サービス所有なので、移設するなら所有者とサービス停止を伴う。
- **エンドポイント移行は既存の稼働環境（RAG の embedding / reranker、OpenCode）に触れる**。移行は読込時に冪等な投影として行い、`llama-runtime.json` は事前にバックアップする。
- `chat.reasoning` の削除は、共通設定で思考を一律 off にしていた運用（現在の値は `"off"`）の挙動を変える。**移行時に、各 LLM モデルの個別 think が未設定なら `off` を書き込む**ことで現在の体感を保つ。
