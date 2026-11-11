# ControlDeckローカルLLMランタイム設計（2026-11-12）

## 概要と実装境界

ControlDeck標準のモデル管理は **Ollama** と **llama.cpp** を維持する。
Luceboxは利用不能のため機能、選択肢、API、Provider、ゲートウェイから撤去した。
Strataは独立した**オプトインのTerminal型ランタイム**として `設定 → オプション機能` に追加する。
インストールだけで既定LLMや推論の実行先、モデル設定が切り替わることはない。

| 機能 | Ollama | llama.cpp | Strata |
|---|---|---|---|
| インストール | 外部サービスとして扱う | GPUランタイム機能 | オプション機能 `strata` |
| モデル管理 | Ollama API | ControlDeck alias/endpoint | Strata Terminalで明示管理（本PRではControlDeckへの自動統合なし） |
| 推論経路 | 既存provider | `/api/v1/llm/v1` | 明示的に起動した場合のみStrata自身のOpenAI互換endpoint |
| 自動起動 | 既存設定 | 既存設定 | なし |
| 更新 | Ollama自身の管理 | `llama-builder` | `strata update --yes` |
| 削除 | 既存方式 | モデル管理画面 | ControlDeck管理CLI/engineのみ、モデルは保持 |

Strata導入先は `data_dir()/runtimes/strata/home` を仮想ホームとして使う。
公式インストーラーはGitHub `Niko1221/Strata` の固定URLから明示的な導入ジョブで取得する。
CLIおよびエンジンの実行はモデルの手動取得・起動と分離する。
データライブラリ `data_dir()/models/strata` はアンインストール時にも削除しない。

## 既存LLMの維持

- `models_mgmt/local_llm.py` の登録モデルはllama.cppのみ。既存のmodel alias、ポート、共有KV受入制御、オンデマンド起動を維持する。
- GPUリソース管理は登録されたllama.cppモデルを引き続き使用する。
- `models_mgmt/runtime_policy.py` の既定モデル・排他ポリシーは変更しない。旧Lucebox選択の読み込み時だけOllamaへ安全に退避する。
- Strataの導入・更新・削除でOllama/llama.cppを停止、更新、削除、切り替えない。
- Strata起動やモデル取得、ControlDeckのゲートウェイ接続を自動化することは今回のスコープ外。

## Strataオプション管理

1. `GET /api/v1/features` はオプションランタイム `strata` と導入済み/未導入を返す。
2. `POST /api/v1/features/strata/install-jobs` はユーザーの明示操作でのみ取得・導入を行い、ジョブ進捗を返す。
3. `POST /api/v1/features/strata/update-jobs` はStrata自身の署名済み更新機能を使う。稼働中モデルは無断停止しない。
4. `POST /api/v1/features/strata/uninstall` は状態を確認し、管理済みCLI/engineのみ削除する。ユーザーのモデル、Ollamaやllama.cppは保持する。
5. `GET /api/v1/features/strata/release-status` で公開リリースとの差分を確認する。通信失敗は状態エラーとして表示する。

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
