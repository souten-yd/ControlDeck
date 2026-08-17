# モデル管理 v2 引き継ぎ資料

最終更新: 2026-08-18
設計本体: `docs/design-model-management-v2.md`
ブランチ: `feature/model-management-v2`

この文書は**作業を別のセッション / 別の AI が引き継ぐため**のもの。設計の「なぜ」は設計書に、
「今どこまで進んでいて、次に何をするか」「実機で確認済みの事実」はここに書く。

---

## 1. 何をやろうとしているか（3 行）

1. think 設定を LLM 共通設定から削除し、モデル個別の詳細設定（レベル + トークンバジェット）にする。
2. 「1 モデル = 1 ポート」をやめ、**エンドポイント**を導入して同一ポートで複数モデルを差し替えられるようにする。順序＝優先度で自動起動を制御する。
3. モデル管理の一般機能（並べ替え・複製・削除・容量表示・HuggingFace ダウンロード・モデルライブラリ）と、評価用の SGLang ランタイムを追加する。

実装順序は設計書「実装順序」章（0〜8）。**0 = NVMe 移設**から着手している。

---

## 2. 進捗

| # | 項目 | 状態 |
|---|---|---|
| 0 | G: NVMe 移設 | **作業中**（§4 参照） |
| 1 | F: モデルライブラリ | 未着手 |
| 2 | A: エンドポイント基盤 | 未着手 |
| 3 | B: think のモデル個別化 | 未着手 |
| 4 | D-1/2/3: 削除・並べ替え・複製 | 未着手 |
| 5 | C: 並列駆動の UI 露出 | 未着手 |
| 6 | フロントエンド分割 | 未着手 |
| 7 | D-4/D-5: 容量表示・HF ダウンロード | 未着手 |
| 8 | E: SGLang | 未着手 |

---

## 3. 実機で確認済みの事実（再調査不要）

**これらは 2026-08-18 に実機で直接確認した。推測ではない。**

### ハードウェア / ストレージ

- GPU: AMD Radeon AI PRO R9700 / **gfx1201 (RDNA4)** / VRAM 32GB / PCI `0000:03:00.0`。ROCm ホスト 7.2.1。
- マザーボード: ASRock **X870 Taichi Creator**。M.2 は 4 スロット（M2_1・M2_2 が Gen5x4、M2_3 が Gen4x4、M2_4 が Gen3x4）。NVMe は 1 本のみ搭載 → **3 スロット空き**。
- **M.2 の物理スロット番号はソフトウェアから取得できない。** `/sys/bus/pci/slots/` はスロット `0`(0000:0f:00) と `0-1`(0000:3f:00) しか公開せず、NVMe (0000:04:00.0) に対応する項目が無い。UI は「接続方式 + モデル名 + 容量」で識別させること。

| マウント | デバイス | 容量 | 空き | 備考 |
|---|---|---|---|---|
| `/` | SATA TOSHIBA THNSN9240GES1 | 218G | **29G (87%)** | OS + ホーム |
| `/data1tb` | **NVMe SanDisk SSD Plus 1TB** (`/dev/nvme0n1p1`) | 916G | **775G (11%)** | UUID `3ebc97b1-f47a-41a4-b8b9-4c62ba55f1a4` |

- `/data1tb` は `/etc/fstab` に UUID 指定・ext4・`defaults,noatime`・pass 2 で登録済み → **起動時に自動マウント**。所有者 `souten:souten 755` で書き込み可。
- `loginctl show-user souten` → `Linger=yes`。ログインなしで boot 時に user manager が起動する。
- ローカル fstab のマウントは `local-fs.target` で完了し、`user@1000.service` はその後に起動する → **user unit 起動時に `/data1tb` は利用可能**。
- ただし **`RequiresMountsFor=` は user unit では機能しない**（user manager 自身の mount unit 名前空間で解決され、システム側マウントを見ない）。`ExecStartPre` の `findmnt` ガードを使うこと。

### `/` を食っているもの（実測）

| 場所 | サイズ | 移設方針 |
|---|---|---|
| `~/ドキュメント/LLM` | 36G | **NVMe に同一ファイルが既存**（§4 参照）→ コピー不要 |
| `/usr/share/ollama/.ollama/models` | 19G | 移す（`OLLAMA_MODELS`・sudo 要） |
| `~/.local/share/control-deck`（data_dir） | 8.5G | 移す（runtimes 3.7G / models 3.5G / features 758M / sdks 578M） |
| `~/.npm` | 5.9G | 移す |
| `~/.cache/ms-playwright` | 1.9G | 移す |
| `~/ControlDeck`（.venv 354M + node_modules 218M 込み） | 630M | 移す |
| `~/.cache/pip` / `~/.cache/uv` | 各 ~390M | 移す |
| `~/.local/share/opencode` | 433M | 移す（symlink を残す） |
| `~/.local/share/searxng` | 136M | **残す**（`SEARXNG_DIR` で移せるが増えない） |
| `~/.config/opencode` | 63M | **残す**（設定・認証） |
| `~/.config/gh`, `/usr/bin/{gh,git}` | 小 | **残す**（OS / apt 管理下） |
| `~/ダウンロード` 7.8G, `~/llama.cpp` 1.3G, `~/.cache/google-chrome` 1.1G | — | **対象外**（ControlDeck 管理外） |

### llama.cpp（同梱ビルド b10001）

`~/.local/share/control-deck/runtimes/llama.cpp/current/llama-server --help` で確認済み:

- `-rea, --reasoning [on|off|auto]`
- `--reasoning-budget N`（-1=無制限 / 0=即終了 / N>0=バジェット）
- `-np, --parallel N`（server slots、default -1=auto）
- `-kvu, --kv-unified` / `--no-kv-unified`
- `-cb, --cont-batching`（default 有効）、`--slots`（default 有効）
- `--chat-template-kwargs STRING`

**`-c/--ctx-size` は全スロットの合計**。`--parallel N` で 1 スロットあたり `ctx_size / n_parallel` になる
（`backend/app/workflows/chat_persist.py:53` のコメントが `/slots` の `n_ctx` を「parallel 分割後の実値」と明記）。
→ `ctx_size=262144, n_parallel=4` で **VRAM を増やさず 64K × 4 並列**が成立する。

### SGLang / ROCm

- **gfx1201 は SGLang の公式サポート対象**。ROCm AI Ecosystem ドキュメントが Radeon 対応 arch に
  `gfx1201 (RX 9070 series, AI PRO R9700/R9600D)` を明記。ROCm 7.14 が Radeon への SGLang 対応を発表。
- 公式の導入手段は **Docker イメージ** `rocm/sgl-dev:v0.5.13.post1-ubuntu24.04-py3.14-rocm7.14`（pip ではない）。
- ホスト要件は amdgpu ドライバ + コンテナランタイムのみ。**ホスト ROCm は 7.14 でなくてよい**（コンテナが ROCm を内包）。
  実機は `/dev/kfd`・`/dev/dri/renderD128` があり、ユーザーは `video`/`render` グループ所属済み → 条件を満たす。
- **Radeon では `SGLANG_USE_AITER=false` と `SGLANG_ROCM_FUSED_DECODE_MLA=false` が必須。**
- 公式注記: 一部モデル（特定の MoE、Qwen3-ASR 等）は Radeon で動作しない。
- **コンテナランタイムは未導入**（docker / podman とも無し）。導入には sudo が要る。
- イメージサイズは同 repo の他タグが 23〜30GB（圧縮時）。※ドキュメント記載のタグは Docker Hub がサイズ未公開のため推定。

### 参考にした一次情報

- SGLang on ROCm: https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/sglang.html
- ROCm 7.14 リリース記事: https://rocm.blogs.amd.com/ecosystems-and-partners/rocm-7.14-blog/README.html
- ASRock X870 Taichi Creator: https://www.asrock.com/mb/AMD/X870%20Taichi%20Creator/index.asp

---

## 4. 進行中の作業: NVMe 移設（実装順序 0）

### 済んでいること

1. **バックアップ取得済み**: `./deck.sh backup` →
   `/home/souten/ControlDeck/backups/control-deck-backup-20260818-081019.tar.gz`（676K）
2. **移設前の状態を退避済み**（scratchpad `premigration/`）: `llama-runtime.json` / `model-runtime-policy.json` /
   `ollama-settings.json` / `config/config.yaml` / 全 `cdapp-llama-*.service` / `control-deck-web.service` /
   稼働 unit 一覧 / enabled 一覧。
   > scratchpad はセッション固有で消えうる。**引き継ぐ場合はこれらを再取得すること**（`./deck.sh backup` が
   > config と DB を含むので、それがあれば足りる）。

3. **移設前の稼働状態**（復元の基準）:
   - 稼働中: `control-deck-web.service`、`cdapp-llama-llama-fc5a1047.service`（alias `llama`, port 8090）、
     `cdapp-4.service`（FrameDeck。ControlDeck 管理アプリ・移設対象外）
   - unit file 状態: `control-deck-web` のみ `enabled`。llama 系は全て `disabled`（auto_start 無効）
   - 登録 instance: `llama`(8090) / `Qwen3.8-27B`(8091) / `embed-bge-m3`(8094) / `rerank-qwen3-4b`(8095)

4. **重要な発見: `~/ドキュメント/LLM` の 36GB は NVMe 側の重複**
   | `/` 側 | サイズ | NVMe 側 |
   |---|---|---|
   | `~/ドキュメント/LLM/Qwen3.6-27B-Q5_K_M.gguf` | 19,834,053,760 | `/data1tb/LLM/Qwen3.6-27B/Qwen3.6-27B-Q5_K_M.gguf` |
   | `~/ドキュメント/LLM/Qwen3.8-27B-UD-Q4_K_XL.gguf` | 17,923,394,624 | `/data1tb/LLM/Qwen3.8-27B/Qwen3.8-27B-UD-Q4_K_XL.gguf` |

   **`cmp` による全バイト比較の結果、2 ファイルとも完全一致（IDENTICAL）を確認済み。**
   → 36GB のコピーは不要。llama instance の `model_path` を NVMe 側へ向け替えるだけでよい。
   `/` 側の 2 ファイルは移設完了・動作確認後に削除すれば 36GB が空く（**ユーザー確認を取ってから**）。

   NVMe 側にのみ存在するモデル（`/` 側に無い）: `Qwen3.6-27B-IQ4_XS.gguf`、
   `Qwen3.6-35B-A3B-Q4_K_M.gguf`、`Qwen3.6-35B-A3B-UD-Q5_K_M.gguf`。
   これらは `files.allowed_roots` に `/data1tb` が無いため **ControlDeck から見えていない**（F で解決する）。

### 次にやること（手順）

設計書「移設の実施手順」の 3 以降。`$VOL` = `/data1tb`。

1. `mkdir -p /data1tb/ControlDeck/{app,data,cache} /data1tb/containers`
2. サービス停止: `systemctl --user stop control-deck-web cdapp-llama-llama-fc5a1047.service`
3. rsync（**旧側は消さない**）:
   - `~/.local/share/control-deck/` → `/data1tb/ControlDeck/data/`
   - `~/ControlDeck/` → `/data1tb/ControlDeck/app/`（`.venv` と `node_modules` は除外）
   - `~/.npm`・`~/.cache/{pip,uv,ms-playwright}` → `/data1tb/ControlDeck/cache/`
   - `~/.local/share/opencode` → `/data1tb/ControlDeck/opencode`（後で元の場所に symlink）
4. `config/config.yaml` を更新:
   - `data_dir: /data1tb/ControlDeck/data` を追加
   - `files.allowed_roots` に `/data1tb` を追加
   - `git_apps_dir: /data1tb/ControlDeck/apps` を追加
   - `application_builder.dotnet_path` を新 data_dir 配下へ（現在 `~/.local/share/control-deck/sdks/dotnet-8.0.423/dotnet`）
5. `~/.config/environment.d/control-deck.conf` と `~/.profile` に環境変数（設計書参照）→ `systemctl --user daemon-reload`
6. `llama-runtime.json` の `instances[].model_path` を NVMe パスへ書き換え
7. 新パスで `./deck.sh service`（`.venv` 再構築 + unit 再生成）
8. `runtimes/llama.cpp/current` symlink を張り直す → 各 llama instance を保存して unit 再生成
9. 起動確認 → **再起動して**自動起動を確認
10. **確認後に**旧ディレクトリ削除（ユーザーへ確認を取る。勝手に消さない）

### 移設で壊れる絶対パス（対処つき）

| 対象 | 対処 |
|---|---|
| `control-deck-web.service` | `./deck.sh service`（`deploy/systemd/control-deck-web.service.in` の `@REPO_ROOT@` を `install_web_unit()` が置換。deck.sh:237-247） |
| `.venv`（`pyvenv.cfg` と shebang） | 移動せず作り直す。`ensure_venv()` が `requirements.txt` の SHA-256 スタンプで自動再構築 |
| `runtimes/llama.cpp/current` symlink | 絶対パス。`llama.switch_backend()` で張り直す |
| `cdapp-llama-*.service` の `ExecStart` | `sync_instance_unit()` / `start_instance()` が毎回書き直すので instance を保存すれば再生成 |
| `llama-runtime.json` の `model_path` | 手で書き換え |
| `config.yaml` の `dotnet_path` | 手で書き換え |

---

## 5. 実装時に必ず踏むポイント

- **設定は DB ではなく JSON 3 ファイル**（`data_dir()/llama-runtime.json`、`ollama-settings.json`、
  `model-runtime-policy.json`）。Alembic revision は不要だが、**既定辞書
  （`llama.DEFAULT_INSTANCE` / `ollama.DEFAULT_SETTINGS` / `ollama.MODEL_CONFIG_KEYS`）へキーを足さないと
  保存が黙って捨てられる**（`save_instance` は `if key in DEFAULT_INSTANCE` でフィルタ）。
- llama.cpp のプロセスは **systemd user unit**。Web プロセスの子にしない（AGENTS.md の規約）。
- **既存テストが旧契約を固定している**。`backend/tests/test_llama_runtime.py` の
  `test_multi_instance_catalog_uniqueness_and_unit_names:71-72`（`pytest.raises(ValueError, match="port 8080")`）と
  API 側 `:199-201`（422 + `"port 8202"`）は、エンドポイント導入で**必ず書き換えが要る**。
- **E2E が UI 文言と DOM 構造に依存**。`frontend/e2e/llama-runtime-settings.spec.ts:48-50`（書き込みキー検証）、
  ダイアログ名 `"${alias} · モデル個別設定"`、`model-provider-common.spec.ts` の
  `getByRole("listitem").first().getByRole("button").first()`。カード構造を変えたら両方直す。
- テストは `./deck.sh test`。サービス再起動は `systemctl --user restart control-deck-web`（**sudo を使わない**）。
- UI 検証は Playwright（`.venv` 導入済み）で 1280 / 390 / 320px。

### 既知のバグ（設計書「今回スコープ外」にも記載。直せるなら直す）

- `llama.py:248` — `save_instance` が `role == "llm"` のとき**常に `selected_alias` を奪う**。
  他モデルを保存しただけで既定チャット先が切り替わる。
- `router.py:755` — `llama_delete_instance` の `gguf_deleted` がハードコードの `False`。
- `RuntimePolicy.default_model_ref` は定義だけで**参照ゼロ**（A で実用化する）。
- `ollama-settings.json` の `idle_unload_enabled` / `idle_unload_minutes` は UI から書けるが効かない
  （実際の idle ループは `RuntimePolicy` を読む）。

---

## 6. ユーザーとの決定事項（変更しないこと）

- ポート共有は「**エンドポイント概念の導入**」で実現する（自動スワップや警告のみ、ではない）。
- think は「**auto + 5 段階 + カスタム**」（`auto / off / low / medium / high / xhigh / custom`）。
  レベルを選ぶとバジェット入力欄に対応値が入り、そのまま微調整できる。
- 並列数（`n_parallel`）は**プリセットではなく自由入力**。
- 追加機能のスコープ: **HuggingFace 直ダウンロード / GGUF 本体の削除・容量表示 / モデル設定の複製**。
  （GGUF メタデータ表示は今回スコープ外）
- 順序（優先度）の用途は「一般的な方法」に委ねられた → 表示順・自動起動・オンデマンド起動の
  フォールバック・既定モデルのフォールバックに効かせる。同時ロード上限超過時の**自動アンロードはしない**。
- **移設は一度きりの作業として手で行う。`deck.sh` サブコマンド化も UI 追加もしない。**
- ストレージは**マウント名やデバイス名を直書きせず**、検出して選択させる。ライブラリは UUID + 相対パスで持つ。
- **OS 側にあるべきものは無理に NVMe へ移さない**（`gh` などのパッケージ管理下のバイナリ、認証情報、設定）。
