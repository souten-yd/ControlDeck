# ControlDeckローカルLLMランタイム設計（2026-11-12）

## 概要と実装境界

ControlDeck標準のモデル管理は **Ollama** と **llama.cpp** を維持する。
Luceboxは利用不能のため機能、選択肢、API、Provider、ゲートウェイから撤去した。
Strataは独立した**オプトインのTerminal型ランタイム**として `設定 → オプション機能` に追加する。
インストールだけで既定LLMや推論の実行先、モデル設定が切り替わることはない。

| 機能 | Ollama | llama.cpp | Strata |
|---|---|---|---|
| インストール | 外部サービスとして扱う | GPUランタイム機能 | オプション機能 `strata` |
| モデル管理 | Ollama API | ControlDeck alias/endpoint | ControlDeckで明示登録、初回起動はジョブ経由 |
| 推論経路 | 既存provider | `/api/v1/llm/v1` | 初回起動後は共通ゲートウェイ `/api/v1/llm/v1` からループバックのStrataへ接続 |
| 自動起動 | 既存設定 | 既存設定 | 初回起動の明示操作後は登録モデルのオンデマンド再起動が可能 |
| 更新 | Ollama自身の管理 | `llama-builder` | `strata update --yes` |
| 削除 | 既存方式 | モデル管理画面 | ControlDeck管理CLI/engineのみ、モデルは保持 |

Strata導入先は `data_dir()/runtimes/strata/home` を仮想ホームとして使う。
公式インストーラーはGitHub `Niko1221/Strata` の固定URLから明示的な導入ジョブで取得する。
CLIおよびエンジンの実行はモデルの手動取得・起動と分離する。
データライブラリ `data_dir()/models/strata` はアンインストール時にも削除しない。

## 既存LLMの維持

- `models_mgmt/local_llm.py` はllama.cppとユーザーが明示登録したStrataモデルだけを列挙する。既存のmodel alias、ポート、共有KV受入制御、オンデマンド起動を維持する。
- GPUリソース管理は登録済みのllama.cpp / Strataを共通のリソース対象とする。Strataは分割重みのため現状は保守的なVRAM予約量を使用する。
- `models_mgmt/runtime_policy.py` の既定モデル・排他ポリシーは変更しない。旧Lucebox選択の読み込み時だけOllamaへ安全に退避する。
- Strataの導入・更新・削除でOllama/llama.cppを停止、更新、削除、切り替えない。
- Strataのモデル登録はユーザー明示操作でのみ作成する。初回起動（大容量ダウンロードあり）は`POST /models/strata/instances/{alias}/start-jobs`で開始する。初回成功前、ゲートウェイからの暗黙モデル取得を禁止する。初回成功後のみ登録モデルのオンデマンド再起動を認める。
- ゲートウェイはStrata別名を上流model id `strata` へ変換し、認証、GPU broker、監査・停止ルールを維持する。Strataの上流サーバーはlocalhost限定とし、LANポートを追加しない。

## Strataオプション管理

1. `GET /api/v1/features` はオプションランタイム `strata` と導入済み/未導入を返す。
2. `POST /api/v1/features/strata/install-jobs` はユーザーの明示操作でのみ取得・導入を行い、ジョブ進捗を返す。
3. `POST /api/v1/features/strata/update-jobs` はStrata自身の署名済み更新機能を使う。稼働中モデルは無断停止しない。
4. `POST /api/v1/features/strata/uninstall` は状態を確認し、管理済みCLI/engineのみ削除する。ユーザーのモデル、Ollamaやllama.cppは保持する。
5. `GET /api/v1/features/strata/release-status` で公開リリースとの差分を確認する。通信失敗は状態エラーとして表示する。

## Strataモデル管理とゲートウェイ

1. `GET /api/v1/models/strata/instances`：導入状態と管理登録モデルを表示。
2. `POST /api/v1/models/strata/instances`：固定のfamily/quant/context/portで登録。任意URL、任意CLI引数、未検証モデルを拒否する。
3. `POST /api/v1/models/strata/instances/{alias}/start-jobs`：明示初回ダウンロード/ロード。モデル取得に必要な時間をHTTPリクエストで保持しない。
4. `POST /api/v1/models/strata/instances/{alias}/stop`：管理モデルを停止する。
5. `POST /api/v1/models/strata/instances/{alias}/delete`：登録情報だけ削除し、モデルファイルは必ず残す。
6. `/api/v1/llm/v1/models` と `/api/v1/llm/v1/chat/completions` から登録Strata別名を選択し、上流のOpenAI互換モデル `strata` にルーティングする。
7. 対応モデル/量子化は現時点のサーバー側allowlistに限定し、Strata側カタログ更新に追従する際はPRでテストする。

## ControlDeck本体の手動更新

- 管理者が`設定 → ControlDeck本体の更新`からGitHubのmainを確認して操作する。
- sourceがGit cloneのmainでoriginが公式かつ作業ツリーがcleanの場合だけfast-forwardする。
- Webサービスとは別のsystemd user unitへ明示ジョブを起動し、依存更新、frontend build、サービス再起動を行う。
- 更新が失敗した場合は保存した旧コミットへ戻すことを試み、結果は`data_dir()/updates/control-deck.json`に記録する。
- 起動時の自動Git更新や自動npm buildは**新設しない**。API権限は`settings.manage`に限定し監査ログへ記録する。

## 安全性とテスト

- インストール対象のURL/コミット/実行ファイルはユーザー指定の任意値を受け付けない。
- 明示操作以外ではモデル重みを取得しない、稼働LLMを停止しない。
- 確認項目：既存Ollama/llama.cppのテスト、Strata管理操作、旧Lucebox経路の非存在、
  本体更新のロールバック、端末/モバイルUI、権限・CSRF・監査。

## Sly-Radiance（2026-11-27）

- 位置づけ: Radiance（`stilldeadcode/radiance`、YAML の設定）とは別物。イメージの entrypoint が vLLM なので、登録の値を `vllm serve` の引数（配列）と `RADIANCE_*` の環境変数に組む（`sly_radiance.serve_args` / `launch_args`）。公式 README の Quickstart の形（W4A8 MXFP4・DFlash2 の先読み・R4D_HYBRID の attention・FULL_DECODE_ONLY の CUDA graph）。
- 共通部品: `container_runtime.py`（podman・イメージ・GPU を渡す `podman run`・systemd unit・lock・health・ready・登録の検証）。Radiance と Sly-Radiance の両方がこれを使い、コンテナで GPU を占有するランタイムの集合は `CONTAINER_RUNTIMES` に 1 つ。
- 並列: `--max-num-seqs` は 1〜8（8 × (先読み 7 + 1) = 64 の検証トークンが `RADIANCE_MXFP4_DECODE_MAX_M=128` の中）。CUDA graph のサイズは同時数 × (先読み + 1) から 8 刻みで作る。並列の枠（`parallel_slots`）は Radiance と同じく登録の値を使う。公式の実測は 1 本 約 154 tok/s、8 本同時で合計 480 tok/s（本機での実測は未）。
- GPU の取り合い: Radiance・Strata・Maya と同時には載らない（Radiance と同じ決まり）。メモリの使用率は共通の VRAM の余裕（`vram_margin_mib`）を引いた値に抑える。
- 画像: 起動で画像を無効にしている（`--limit-mm-per-prompt.image 0`）ので vision は使えない。
- 未確認: 実機の起動・並列の測定・kv4・`reasoning_effort: none`・`/tokenize` の形。
