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
| 0 | G: NVMe 移設 | **完了**。残は再起動での自動起動確認のみ（ユーザーが手動実施） |
| 1 | F: モデルライブラリ | **完了**（§4.5 参照） |
| 2 | A: エンドポイント基盤 | **バックエンド完了**。UI は未着手（§4.6） |
| 3 | B: think のモデル個別化 | **完了**（§4.7） |
| 4 | D-1/2/3: 削除・並べ替え・複製 | **完了**（§4.10） |
| 5 | C: 並列駆動の UI 露出 | **完了**（§4.8） |
| 6 | フロントエンド分割 | 未着手 |
| 7 | D-4/D-5: 容量表示・HF ダウンロード | **バックエンド完了**（§4.13）。UI は未着手 |
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

### 実施済み（2026-08-18）— **移設完了。アプリは NVMe 側で正常稼働中**

`$VOL` = `/data1tb`（SanDisk 1TB NVMe, UUID `3ebc97b1-…`）。

1. キャッシュ rsync（サービス稼働のまま）→ サービス停止 → `data_dir`(8.5G/40 秒)・リポジトリ・
   opencode を rsync。**コピーで行い、旧側は最後まで残した**。
2. `config/config.yaml` を更新。**`allowed_roots` は `/data1tb` 全体ではなく絞った**:
   ```yaml
   data_dir: /data1tb/ControlDeck/data
   git_apps_dir: /data1tb/ControlDeck/apps
   files:
     allowed_roots:
       - /home/souten
       - /data1tb/LLM               # モデルライブラリ
       - /data1tb/ControlDeck/app   # リポジトリ
       # /data1tb/ControlDeck/data（data_dir）は意図的に含めない
   application_builder:
     dotnet_path: /data1tb/ControlDeck/data/sdks/dotnet-8.0.423/dotnet
   ```
   > `files/service.py:_deny_roots()` は `data_dir()` を**動的参照**するので DB と `secret.key` は
   > 移設後も自動保護される。それでもバックアップや設定 JSON を Files に見せる必要はないため
   > data_dir 自体を許可ルートから外した（移設前より狭い）。
3. `llama-runtime.json` の `model_path` を 4 件とも NVMe へ書き換え（`.pre-migration.bak` を隣に残した）。
4. **コード変更 (1)**: `deploy/systemd/control-deck-web.service.in` に
   `ExecStartPre=/usr/bin/findmnt --mountpoint @DATA_MOUNT@` を追加し、`deck.sh` の
   `deck_data_dir()` / `data_mount()` で置換する。既定構成では `/` に解決され常に成功する。
5. 新パスで `./deck.sh service` → `.venv` 再構築・フロントビルド・unit 再生成。
6. `llama.switch_backend()` で `runtimes/llama.cpp/current` symlink を新 data_dir へ張り直し →
   `sync_instance_unit()` で全 `cdapp-llama-*.service` を再生成。
7. `~/.local/share/opencode` を `/data1tb/ControlDeck/opencode` への symlink に置換。
8. **コード変更 (2) — 途中で判明した問題への恒久対策**:
   `~/.profile` は**非ログインシェルで読まれず**、`~/.config/environment.d` は
   **systemd unit にしか効かない**。そのため `./deck.sh service` を非ログインシェルから実行すると
   Playwright Chromium が旧 `~/.cache/ms-playwright` に入ってしまった（実際に発生し 2.5G に膨らんだ）。
   → **`deck.sh` に `export_cache_paths()` を追加**し、main dispatch の直前で呼ぶようにした。
   キャッシュ先は `deck_data_dir()/cache` に統一（data_dir を大容量ドライブへ向ければ自動追随）。
   利用者が明示設定済みの変数は上書きしない。`backup.sh` は data_dir 全体ではなく
   `secret.key`/`rag`/`icons`/DB/config/unit だけを取るので、キャッシュを配下に置いても
   バックアップは肥大しない（確認済み）。
9. 旧ディレクトリを削除（ユーザー承認済み）。**`/` の空き 28GB → 81GB**。
   削除: `~/ドキュメント/LLM`(36G) / `~/.local/share/control-deck`(8.5G) / `~/.npm`(5.9G) /
   `~/.cache/{pip,uv,ms-playwright}`(3.3G) / `~/.local/share/opencode.pre-migration`(433M) /
   `~/ControlDeck`(631M)。

**動作確認済み**: Web `HTTP 200` / `ExecStartPre=findmnt --mountpoint /data1tb` が status=0/SUCCESS /
新 data_dir の DB が更新され旧側は停止 / `llama`(8090) が NVMe の GGUF から生成成功（`1+1`→`2`、`3*7`→`21`）/
embedding(8094) 1024 次元・reranker(8095) がオンデマンド起動 / OpenCode symlink 経由で参照可 /
`pip cache dir` が新パスを返す。**削除後にも全項目を再確認した。**

### 現在の実際のレイアウト（これが正）

```
/data1tb/ControlDeck/
  app/            リポジトリ（git 作業ツリー。旧 ~/ControlDeck は削除済み）
  data/           data_dir
    cache/        pip / uv / npm / ms-playwright / huggingface   ← deck.sh が指す
    runtimes/     llama.cpp・whisper.cpp
    models/gguf/  bge-m3・Qwen3-Reranker
  opencode/       ~/.local/share/opencode はここへの symlink
/data1tb/LLM/     GGUF（llama・Qwen3.8-27B の参照先）
/data1tb/containers/  SGLang 用（空）
```

**以降の作業は `/data1tb/ControlDeck/app` で行う。`~/ControlDeck` はもう存在しない。**

### 残っていること

1. **再起動して自動起動を確認する**（未実施。ユーザーが帰宅後に手動で再起動する）。
   確認観点: `systemctl --user status control-deck-web` が active で、
   `ExecStartPre=/usr/bin/findmnt --mountpoint /data1tb` が status=0/SUCCESS であること。
2. 特権 helper（`/usr/local/libexec/control-deck-hw-helper`）は**元々未登録**。移設の影響ではない
   （設置先は repo 非依存）。AMD GPU 電力プロファイルを効かせるには、ユーザーが対話端末で
   `./deck.sh service` を実行して sudo 認証する必要がある。
3. Ollama のモデル 19GB は `/usr/share/ollama/.ollama/models` に残っている。移すなら
   `sudo systemctl edit ollama` で `Environment=OLLAMA_MODELS=/data1tb/LLM/ollama` を設定し、
   ディレクトリを `ollama:ollama` 所有にする。**sudo が要るのでユーザーの明示実行**。
4. SearXNG(136M) と `~/.config/opencode`(63M) は判断どおり `/` に残してある。

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

## 4.5 F: モデルライブラリ（実装順序 1）— 完了

### 追加したもの

- `backend/app/models_mgmt/libraries.py`（新規）
  - `detect_volumes()`: `lsblk -J -b -e7` を読み `shutil.disk_usage` で空きを付ける。新規依存なし。
    `/boot` `/efi` `/snap` `/var/snap`、squashfs/cifs/nfs 等、UUID 無しは候補から除外。
    transport / model はディスク側（親ノード）から引き継ぐ。
  - `library_path()` / `default_library_id()` / `default_models_dir()` / `scan_library()` /
    `list_libraries()` / `validate_entries()`。
  - **未マウントのボリュームは「未接続」を返し、system ドライブへ暗黙フォールバックしない**
    （マウント漏れで / を埋める事故を防ぐ。テストで固定済み）。
  - 既定ライブラリが未接続なら、接続済みで最も空きの大きいものへ退避する。
- `runtime_policy.ModelLibrary` + `RuntimePolicy.model_libraries`（空なら builtin 1 件を合成＝後方互換）。
- API: `GET /models/storage/volumes` / `GET /models/libraries` / `PUT /models/libraries` /
  `GET /models/libraries/{id}/scan`。**`/models/{model:path}` 系より前に定義する**こと（吸われるため）。
- `role_presets._models_dir()` が既定ライブラリを使う（未接続時は従来の data_dir 配下へフォールバック）。
- フロント: `frontend/src/api/models.ts`、`frontend/src/features/models/ModelLibraryPanel.tsx`。
  共通設定シート（`SettingsSheet`）の末尾に差し込んである。
- テスト: `backend/tests/test_model_libraries.py`（12 件）。

### 実機の設定値（現在の `model-runtime-policy.json`）

```json
"model_libraries": [
  {"id":"main","label":"モデル (NVMe)","volume_uuid":"3ebc97b1-…","subpath":"LLM","default":true},
  {"id":"builtin","label":"内蔵 (Embed/Reranker)","path":"/data1tb/ControlDeck/data/models/gguf"}
]
```

結果: `main` = **GGUF 5 件 102GB（うち未登録 3 件）**、`builtin` = 2 件 4GB。
未登録の 3 件は `Qwen3.6-27B-IQ4_XS` / `Qwen3.6-35B-A3B-Q4_K_M` / `Qwen3.6-35B-A3B-UD-Q5_K_M`。

### この作業中に判明したこと

`allowed_roots` から data_dir 全体を外したため、**旧来の保存先 `data_dir/models/gguf` が
スキャンできなくなった**（`ollama.scan_gguf` が `files.resolve` を通るため）。
`/data1tb/ControlDeck/data/models` だけを `allowed_roots` に追加して解決した。
DB・バックアップ・設定 JSON は引き続き許可ルートの外にある。

### 残（F の範囲で未実装。UI 側の作り込み）

- 未登録 GGUF からの**ワンタップ登録**は、一覧表示までは出来ているが登録ボタンは未実装。
  既存の `POST /models/llama/instances` を呼べばよく、新しい API は不要
  （エンドポイント概念を入れる A の後にやる方が手戻りがない）。
- 孤児 GGUF の削除 UI は D-1（GGUF 実体削除）と一緒に実装する。

---

## 4.6 A: エンドポイント基盤（実装順序 2）— バックエンド完了

### 入れたもの

- `llama-runtime.json` に `endpoints: {id: {id,label,port,active_alias}}`。instance に
  `endpoint_id` と `order`（1始まり・小さいほど優先）を追加。
- `_migrate_endpoints()` が読込時に **port から冪等に投影**する。移行前は port が一意だったので
  1:1 で無損失。`order` は既存の並び順で 1..N を採番。実機の 4 instance で移行を確認済み。
- `save_instance()` の **port 一意制約を撤廃**。同一 port は同一エンドポイントに束ねる。
  `port` 直接指定は互換のため受け付け、該当エンドポイントが無ければ作る。
  `port` は endpoint を正とする派生値として各 instance へ写す。
- `start_instance()` が起動前に**同一エンドポイントの他モデルを停止**する。
  起動成功後は `active_alias` を記録する。
- `instance_for_port()` / `resolve_instance_by_port()`：稼働中 → `active_alias` → 最優先、の順で
  ポートを代表するモデルを1件に決める。`list_instances()` ベースなので
  テストの monkeypatch がそのまま効く。
- `_sync_endpoint_units()`：エンドポイント内で `auto_start` が複数あっても
  **enable するのは最優先の1件だけ**（boot 時の同時起動によるポート競合を防ぐ）。
- `reorder_instances()` / `duplicate_instance()`。
- `providers.py` をエンドポイント単位のグルーピングに変更（同居モデルが1件へ潰れていた）。
- API: `GET /llama/endpoints` / `PUT /llama/endpoints/{id}` / `POST /llama/endpoints/{id}/delete`（所属あれば409）/
  `POST /llama/instances/reorder` / `POST /llama/instances/{alias}/duplicate`。
  `LlamaInstanceBody` に `endpoint_id` / `order` を追加し、共通設定APIの `forbidden` にも加えた。

### 実装中に判明して設計を変えた点

1. **同一 GGUF の重複登録禁止を撤廃した**。設計書では「endpoint と model_path の組が同じときだけ拒否」
   としていたが、それは複製機能の主目的（同じ GGUF を別 CTX 設定で持って切り替える）を
   そのまま塞ぐ。ポートが一意だった頃は「同じファイルで2つのサーバーが立つ」のを避ける意味が
   あったが、エンドポイント内は排他起動になったので同時に動くことはない。
   識別子としての一意性は alias で担保する。
2. **`save_instance` が selected_alias を奪う問題を直した**（既知バグ）。
   新規登録時は従来どおり引き継ぎ、**既存モデルの保存では奪わない**。
3. `_ensure_port_free_for_other_runtimes()` を追加。従来は llama instance 同士しか見ておらず、
   Ollama のポートを指定しても保存は通り起動して初めて失敗していた。

### 実機で確認したこと

- 既存 4 instance が `ep-8090/8091/8094/8095` へ無損失移行。
- `Qwen3.8-27B` を `ep-8090` へ移して `llama` と同居 → `start_instance` で
  **`llama` が自動停止して切り替わり**、`/v1/models` が `['Qwen3.8-27B']` になり生成成功。
  **endpoint (`http://127.0.0.1:8090/v1`) は変わらない**（OpenCode 等の接続先固定が目的）。
- 検証後は元の構成へ戻してある。

### 残（A の範囲）

- **UI 未実装**: エンドポイント管理・並べ替え（↑↓）・複製。設計書「UI/UX 設計」の
  Models 画面カード刷新（実装順序 6）と一緒にやる方が手戻りがない。
- `default_model_ref` のフォールバック実装（優先度最上位を既定にする）は未着手。

---

## 4.7 B: think のモデル個別化（実装順序 3）— 完了

### 語彙（`backend/app/models_mgmt/thinking.py` が正）

`auto / off / low / medium / high / xhigh / custom`。
レベル→バジェット: low=1024 / medium=4096 / high=16384 / xhigh=32768。
`auto` は「何も送らない」（モデル既定に任せる）、`custom` は `think_budget_tokens` を直接指定。
旧語彙は読み替える: `""`→auto、`on`/`true`→**high**、`max`→xhigh、`false`→off。

### ランタイムごとの伝達

| ランタイム | 方法 |
|---|---|
| llama.cpp | instance の CLI 引数。`off`→`--reasoning off` / レベル・custom→`--reasoning on --reasoning-budget N` / `auto`→引数なし。**unit 引数なので保存後の再起動が要る** |
| Ollama | native `/api/chat` の `think`。バジェット非対応なので最も近いレベルへ落とし、`xhigh` は `high` へ寄せる |
| 外部 OpenAI 互換 | `reasoning_effort`（`RuntimeChatRequest.reasoning_effort` を新設）。`disable_thinking` 時は送らない |

### 変更点

- `RuntimePolicy.ChatDefaults.reasoning` を**削除**（`timeout_seconds` は残る）。
- `chat_router._resolve_think` → `resolve_think(base_url, model, requested)` に置換。
  **共通設定は参照しない**。リクエストで明示された場合だけそれを優先する。
  `_think_for` は不要になったので削除。
- `chat_persist` は `body.thinking or ""` にし、未指定ならモデル個別設定へ委ねる。
- `ollama.normalize_think` / `effective_think` は thinking.py へ委譲。
- 保存キー: llama instance に `think` / `think_budget_tokens`、
  `ollama.MODEL_CONFIG_KEYS` に `think_budget_tokens` と `order`、
  `provider_adapters._LLAMA_CONFIG_KEYS` にも追加。
- フロント: `features/models/ThinkingControl.tsx`（両ランタイム共通）。
  共通設定シートの「チャット思考」セレクトは削除し、移動した旨の案内に置き換えた。

### 一度きりの移行（実装済み・実機で実行済み）

`thinking.migrate_shared_reasoning()` を `main.py` の lifespan から呼ぶ。
旧 `chat.reasoning` を、**個別 think が未設定の LLM モデルにだけ**書き、その後キーを削除する。
共通設定を消しただけだと「共通でオフ」だった環境が突然思考し始めて体感が変わるため。
embedding/reranker は対象外。2 回目以降は何もしない。

実機ログ: `思考設定をモデル個別へ移行しました: mode=off models=llama.cpp:Qwen3.8-27B, ollama:qwen3.6-27b-q5_k_m:latest`
（`llama` は検証中に手動で off にしていたので対象外だった）

### 実機で確認したこと

同じ質問（17×23）で llama.cpp を再起動して比較:

| 設定 | reasoning | completion | 答え |
|---|---|---|---|
| `think=off` | 0 文字 | **4 tok** | 391 |
| `think=high` | 312 文字 | 139 tok | 391 |

`--reasoning on --reasoning-budget 16384` が b10001 に受理されることも確認済み
（ログにエラーなし）。

> 注意: `start_instance` 直後に `/health` を叩くと**まだ旧プロセスが応答している**ことがある。
> 再起動の確認は `until [ "$(curl ... /health)" = 200 ]` で待つこと。

---

## 4.8 C: 並列駆動（実装順序 5）— 完了

### 結論: `--parallel` は起動引数。実行中の変更はできない

- `llama-server --help` は `-np, --parallel N` を**起動引数**としてのみ持つ。
- `/props` は `total_slots` を**読み取り専用**で返すだけ。`POST /props` は既定で無効
  （`--props` で有効化しても対象は限定的）。実機で **501** を確認。
- KV キャッシュはロード時に `ctx_size` と slot 数から確保されるので、
  slot 数の変更は再確保＝再ロードが要る。

**ただし再起動は安い**。モデルファイルが OS の page cache に残るため、
実機（20GB Q5_K_M）で **ウォーム再起動 25 秒**だった。運用上は「切り替えは数十秒」でよい。

### 実機で確認した CTX 分割

`ctx_size=262144` のまま `n_parallel` を変えたときの `/slots`:

| n_parallel | slots | n_ctx / slot |
|---|---|---|
| 1 | 1 | 262,144 |
| 4 | 4 | **65,536** |

**VRAM は増えない**（合計 CTX は同じ）。4 本同時リクエストを流して全て正答、合計 1 秒で完了。

### UI

`LlamaInstanceControls` の「よく使う」段に:
- 「同時リクエスト数（スロット）」= `n_parallel` の**自由入力**（1〜64）
- その直下に実効値をライブ表示: 「CTX 262,144 は全スロットの合計です。
  1リクエストあたり **65,536** × 4 並列になります（VRAM は増えません）」

### この作業中に見つけて直したバグ（重要）

**保存した設定が反映されないことがあった。**
`start_instance` は unit ファイルとの差分で `changed` を判定して
`restart if active and changed else start` としていたが、
`save_instance` → `_sync_endpoint_units` が**先に unit を書き出す**ため
`changed` は常に False になり、稼働中は `systemctl start`（no-op）に落ちていた。
結果、`n_parallel` を変えても古いプロセスが動き続ける。

→ **稼働中は必ず restart する**ように変更。この関数は「設定を適用して起動する」意図で
呼ばれるので作り直すのが正しい。単なる起動保証（`ensure_ready`）は health 済みなら
手前で返るため、無駄な再起動にはならない。
回帰テスト: `test_start_instance_restarts_running_unit_to_apply_saved_settings`。

> このバグは移設前から存在していた（`sync_instance_unit` も unit を書いていたため）。

---

## 4.9 共有KV（kv_unified）と受け入れ制御 — 完了

### 実機で確定させた llama.cpp の挙動

`--kv-unified --ctx-size N --parallel P` のとき:

- **`/slots` の `n_ctx` は各slotの「上限」であって予約ではない**。P個のslotが全部 N を返すが、
  総容量が N×P になるわけではない。実体は**合計 N の共有プール**。
  起動ログも `n_slots = 4, n_ctx_slot = 8192, kv_unified = 'true'` と出る。
- **非対称な配分ができる**（これが共有KVの狙い）。ctx=8192 での実測:

  | 構成 | 結果 |
  |---|---|
  | 単発 6,844 tok | 200 成功 |
  | 5,012 + 1,042 = 6,054（74%） | **両方 200 成功** |
  | 約 7,950（97%） | 両方 500 |
  | 6,844 × 2 = 13,688 | 両方 500 |

  実機 ctx=262,144 / parallel=4 でも 5,014 + 614 の非対称同時実行が成功。
- **枯渇はキューイングされず即エラー**。しかも**実行中の他リクエストごと巻き込んで失敗する**。
  失敗の形は2種類あり、区別が要る:
  - 単一リクエストが CTX 超過 → **400** `request (10015 tokens) exceeds the available context size (8192 tokens)`（再試行しても無駄）
  - 同時実行の合計で枯渇 → **500** `Context size has been exceeded.`（空けば通る＝再試行の価値がある）
- slot 数を超えるリクエストは `requests_deferred` として**正常にキューイングされる**
  （4 slots に 6 本投げて processing=4 / deferred=2、全件成功）。
  **枯渇と混同しないこと**: slot 不足は待つ、KV 不足は落ちる。

> 97% で落ちる理由（出力分の予約・プロンプトキャッシュ・断片化のいずれか）は切り分けていない。
> 正確な予測に依存しない設計にしてあるので、実装上は問題にならない。

### 実装

- instance に `kv_unified`（既定 True）。`--kv-unified` / `--no-kv-unified` を出し分ける。
- `--metrics` を常時付与（読み取り専用。空き容量の観測に使う）。
- `llama.endpoint_capacity(port)`: `/slots` の稼働中slotから
  `Σ(n_prompt_tokens + next_token[0].n_decoded)` で使用量を出し、`/metrics` の
  `requests_deferred` を添える。`KV_HEADROOM_RATIO = 0.85` の余白を引いた `usable` で判定。
- `llama.await_capacity(port, needed, timeout)`: 空くまで待つ。**busy=0 なら即返す**
  （単発の大きなリクエストを妨げない）。
- `LlamaCppRuntimeProvider._wait_for_capacity()` を生成前に呼ぶ。
  さらに **500 を掴んだら待って投げ直す**（`_CAPACITY_RETRIES = 3`）。
  空き容量は正確に予測できないため、予測を厳しくするより弾かれてから吸収する方針。
  400 は再試行しない。共有KVを持たない provider（Ollama / 外部）は再試行しない。
- API: `GET /models/llama/endpoints/{id}/capacity`。
- UI: 「最大同時リクエスト数」+「KVを共有プールにする（推奨）」トグル。
  共有時と分割時で説明を出し分ける。

### 注意

`--parallel` は**起動引数**で、実行中は変更できない（`POST /props` は 501）。
KV はロード時に確保されるため。ただしウォーム再起動は実測 25 秒。

---

## 4.10 D-1/2/3: 削除・並べ替え・複製（実装順序 4）— 完了

- **削除が UI から到達不能だった**。一覧の削除ボタンは `selectedProvider === "ollama"` で
  ガードされ、`LlamaRuntimePanel` の削除は `!registrationOnly` 分岐（描画されない）にあった。
  一覧行に `DropdownMenu`（詳細設定／複製／削除）を置いて到達可能にした。
- `llama.delete_instance(alias, delete_file=False)`。**本体削除は取り消せない**ので、
  許可ルート内であることと**他のモデル設定が同じファイルを参照していないこと**を確認してから消す。
  `router.py` のハードコード `gguf_deleted: False` を実値にした。
- 並べ替えは ↑↓ ボタン（`MobileNavigationSettings` の流儀）。`POST /llama/instances/reorder`。
- 複製は `DuplicateDialog`。既定で**同じエンドポイントに載る**ので CTX 違いの切替に使える。
- 同一エンドポイントを共有する行には `:8090 共有` バッジを出し、**ロード時に同居モデルが
  止まることを確認ダイアログで先に知らせる**（接続先は変わらない旨も明記）。
- 削除で `selected_alias` を巻き込まないよう修正（使っていないモデルを消しただけで
  既定チャット先が変わらない）。

---

## 4.11 OpenCode / OMo への受け入れ制御の適用 — 完了

**§4.9 の admission control は `LlamaCppRuntimeProvider` の中にあるため、
ControlDeck を経由する生成（Chat / Workflow / RAG）にしか効かない。**

実機の設定を確認したところ:

```json
// data/integrations/opencode/settings.json
{"base_url": "http://127.0.0.1:8090/v1", "model": "llama"}
```

`integrations/opencode/provider.py:82` がこの baseURL を OpenCode の runtime config へ書き、
OpenCode は **llama.cpp を直接叩く**。`provider.py:303` の `ensure_ready_by_base_url` は
起動保証だけで、KV の空き待ちも 500 の再試行も通らない。

結果として、OMo のようにサブエージェントを並列で走らせると:

- slot は 4 まで埋まる（slot 不足ではないので llama.cpp は queue に逃がさない）
- 共有KVが枯渇すると `500 Context size has been exceeded` になり、
  **実行中の他リクエストごと巻き込んで失敗しうる**

### 対応（実装済み）

ControlDeck に **OpenAI 互換ゲートウェイ**を置き、OpenCode の接続先をそちらへ向ける。

```
OpenCode / OMo → ControlDeck gateway → (admission control) → llama.cpp :8090
```

`endpoint_capacity()` / `await_capacity()` をそのまま流用できる。
**要決定: 認証方式**。OpenCode は `apiKey: "sk-no-key"` を送るだけで、
ControlDeck はセッション Cookie 認証なので、そのままでは通らない。

### 併せて検討する精度改善（未実装）

現在の必要量見積りは `prompt_chars // 4 + max_tokens` の概算。
コードエージェントは `max_tokens=16384` でも実際は 2,000 で終わることが多く、
全量を予約すると実効並列度を不必要に下げる。

`needed = actual_prompt_tokens + min(max_tokens, predicted_output_p95)`
のように、実測 p95 を使うと並列度を上げられる。
tokenizer による正確な入力トークン数、prompt cache、`requests_deferred` も材料になる。

---

## 4.12 OMo（oh-my-openagent）アドオン — 完了

- npm パッケージ **`oh-my-openagent`**（実行ファイル `omo`、v4.19.4 で確認）。
  OpenCode のプラグインとして動くため `requires: "opencode"` を持たせ、
  未導入なら導入ボタンを止めて順序を案内する。
- 設定画面のアドオン一覧は `/features` を汎用に描画しているので、
  **登録するだけで導入・更新・削除ボタンが付く**（UI 側の追加は依存表示のみ）。
- **並列数の考え方**: OMo の並列数を llama の `--parallel` に一致させる必要はない。責務を分ける。

  | レイヤー | 役割 |
  |---|---|
  | OMo | いくつの仕事を並行して進めたいか（論理並列） |
  | ControlDeck | いま GPU へ何本入れて安全か（受付・待ち行列） |
  | llama.cpp | 実際の同時実行（slot と共有KV） |

  エージェントは常に LLM を呼んでいるわけではない（grep やビルドの時間がある）ので、
  **論理並列 > slot 数は健全なオーバーサブスクリプション**になり GPU を遊ばせにくい。
  溢れた分はゲートウェイの受け入れ制御が待たせる。

  - **ゲートウェイ経由（既定）**: OMo 既定のまま `default_concurrency=5` /
    `team.max_parallel_members=4`。slot 数に縛らない。
  - **llama.cpp 直結**: 誰も待たせてくれないので `max(1, n_parallel - 1)`。
    対話中のメインエージェント用に 1 本空ける。

- **設定先は `~/.omo/omo.jsonc`**（`~/.config/opencode` ではない）。
  OMo 自身の `config migrate` の移行先もここ。
- **現行スキーマは `task.*`**。`background_task.defaultConcurrency` は**旧式**。
  schema は **`.strict()`** なので未知のキーを混ぜると設定ごと弾かれる。
  実測した現行スキーマ（v4.19.4）:

  ```
  task.default_concurrency        (default 5)
  task.provider_concurrency       (optional)
  task.model_concurrency          (optional)
  task.team.max_members           (default 8)
  task.team.max_parallel_members  (default 4)
  ```

  解決順は `model_concurrency > provider_concurrency > default_concurrency` なので、
  **既定値だけ書き、利用者が付けた個別上書きは残す**。jsonc のコメントも壊さない。
- 同期の起点は2つ: OMo 導入時と、**OpenCode が使っているモデルの `n_parallel` を変えたとき**。
  関係ないモデルの変更では触らない。

### 実機での動作確認（2026-08-18）

| 項目 | 結果 |
|---|---|
| 設定画面からの導入 | 成功（install-job succeeded） |
| version / health | 4.19.4 / healthy |
| 実行ファイル | 動作。**bun 非導入のため Node CLI へフォールバック**（正常。警告が出るだけ） |
| 導入直後の自動設定 | ゲートウェイ経由なので `task.default_concurrency: 5` / `team.max_parallel_members: 4` |
| 直結時の保守的な値 | `n_parallel` 4 なら 3（メイン用に1本確保） |
| OMo によるスキーマ検証 | `omo config migrate --dry-run --json` が `diagnostics: []` / 競合0で受理 |
| ゲートウェイ経由の並列生成 | 3本同時すべて HTTP 200・正答、1秒 |

導入先: `data/features/omo/node_modules/oh-my-openagent`。
OMo 設定: `~/.omo/omo.jsonc`（ホーム配下。data_dir ではない）。

---

## 4.13 D-5: HuggingFace 直ダウンロード（実装順序 7）— バックエンド完了

`backend/app/models_mgmt/hf.py`（新規）。`huggingface_hub` には依存せず httpx のみ。

- `search_models(q)` / `list_repo_files(repo, revision)` / `download(job, repo, files, ...)`
- **分割GGUF**（`model-00001-of-00003.gguf`）は 1 グループにまとめ、合計サイズと
  必要な全ファイルを返す。1つだけ落としても使えないため。
  欠けている場合は `complete: False` で示す。
- **開始前に空き容量を検査**（`_ensure_free_space`、1GB の予備を残す）。
  入らないものを落とし始めると中途半端なファイルが残る。
- `.part` へ書いて `replace()` で原子的に公開。**Range ヘッダでレジューム**、
  レンジ非対応なら取り直す。
- 既に全ファイルがあれば**接続すら張らない**。
- gated repo 用トークンは暗号化して保存（`gateway._load/_save` の設定を共用）。
  値はログにも監査にも残さない。
- API: `GET /models/hf/search` / `GET /models/hf/repos/{repo:path}/files` /
  `GET,PUT /models/hf/settings` / `POST /models/hf/download-jobs`。

### 保存先とレイアウト

`<選択したライブラリ>/{owner}--{name}/{file}.gguf`。
`role_presets.install` もこの `hf.download` へ委譲した（取得処理の重複を消し、
レジュームと空き容量検査を共通で受けられる）。

> **注意**: これでレイアウトが変わったため、`role_presets` の導入済み判定を
> 全ライブラリ × (repo別サブディレクトリ | 直下の平置き) で探すようにした。
> 既定ライブラリを NVMe へ移した結果、旧 data_dir 配下の既存 GGUF を見失って
> 再ダウンロードになる問題が実機で出たため。

### 実機で確認したこと

- `gpustack/bge-m3-GGUF` のバリアント一覧（FP16 1158MB 〜 Q2_K 366MB）を取得
- Q2_K（350MB）を `main` ライブラリへダウンロード成功 →
  ライブラリの GGUF 件数が 5→6 に反映
- 同じ指定で再実行すると「既にあります」で再取得しない

### 残（UI 未実装）

`PullSheet` の HF タブを「Ollama へ pull」と「GGUF を直接ダウンロード」の2系統に分ける。
repo 検索 → バリアント一覧（サイズと選択中ライブラリの空き容量を並べ、入らないものは
選べない）→ alias / エンドポイント指定 → ジョブ開始。
進捗は job kind が `model.` 始まりなので既存の `useModelJobsStream` が拾う。

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
