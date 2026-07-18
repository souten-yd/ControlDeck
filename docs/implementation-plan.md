# 実装計画

要求仕様: [requirements.md](requirements.md) / 進捗: [implementation-status.md](implementation-status.md)

## Phase 構成（要求仕様 §29 準拠）

### Phase 1 — 基盤（認証 + レイアウト）
- FastAPI + SQLite（SQLAlchemy 2.x、WAL）
- ログイン / ログアウト / サーバー側セッション（Argon2id、HttpOnly Cookie、CSRF 対策）
- 管理者作成 CLI（scripts/create-admin.sh）
- RBAC（administrator / operator / viewer + 権限依存性）
- 監査ログ基盤
- React + Vite + Tailwind。デスクトップサイドバー / モバイル下部ナビ / グローバル操作シート
- 基本ダッシュボード

### Phase 2 — アプリ管理
- アプリ登録（Python Script / Shell Script / Executable。名前 / Python パス / スクリプト / 引数 / 作業ディレクトリ / 環境変数 / 自動起動 / 再起動ポリシー / 停止タイムアウト）
- systemd ユーザーユニット生成（安全テンプレート、`cdapp-{id}.service`）
- 起動 / 停止 / 再起動 / 強制終了、状態マッピング（STOPPED〜UNKNOWN の 8 状態）
- stdout / stderr のファイル保存、ログ表示 / ストリーム / ダウンロード / 削除
- Python 自動検出（候補提示のみ）
- アプリ一覧カード UI（PC / モバイル）、アプリ追加フロー（3 ステップ）
- アプリアイコン（PNG / JPEG / WebP / サニタイズ済みSVG、認証付き配信）
- 15秒間隔および手動のヘルスチェック（プロセス / TCP / HTTP status・本文 / 許可ルート内ファイル）、失敗時DEGRADED

### Phase 3 — 監視
- CPU / RAM / ディスク / ネットワーク / 稼働時間（psutil）
- GPU / VRAM（AMDは低負荷sysfs fast path → amd-smi / rocm-smi fallback、NVIDIAはnvidia-smi、失敗時 N/A）
- 消費電力推定、単一メトリクス WS ストリーム、履歴（生 24h / 1 分平均 30 日）
- アラート（しきい値 + 通知センター）
- 電源予約は予約時だけ systemd ユーザーtimerを生成（Web再起動・SSH切断後も継続、期限切れは再実行せず、取消時削除）

### Phase 4 — ファイル + ターミナル
- 許可ルート限定ファイルマネージャー（一覧 / 再開可能チャンクアップロード / ダウンロード / 編集 / コピー / 移動 / ごみ箱）
- ごみ箱（復元 / 完全削除 / 保持期間 / 容量上限）とアップロード進捗・中止・再開
- Monaco 遅延ロード、Web ターミナル（xterm.js + tmux、モバイル補助キーバー）

### 自己メンテナンス / ウォッチドッグ（2026-07-12 ユーザー要望で追加）

本体自身の健全性維持を自動化する。

- **systemd ウォッチドッグ**: control-deck-web を `Type=notify` + `WatchdogSec=30` で運用。
  アプリは内部ヘルスチェック（DB 接続 / メトリクス収集の鮮度 / スケジューラー心拍）が正常な間だけ
  `WATCHDOG=1` を送信し、ハング・内部異常時は systemd が自動再起動する
- **自己メンテナンスループ**（1 時間間隔 + 起動 5 分後に初回）:
  - 管理アプリログのローテーション（copytruncate 方式 + gzip、`logs.rotate_size_mb` / 世代数 / 保持日数）
  - 期限切れ・失効セッションの purge
  - 監査ログの保持期間超過分の削除（`logs.audit_retention_days`、既定 180 日）
  - SQLite の WAL checkpoint + PRAGMA optimize
  - data_dir のディスク残量自己点検（10% 未満で警告ログ）
- **自己状態 API**: `GET /system/self-status`（認証必須）でウォッチドッグ有無・各チェック結果・
  最終メンテナンス実行時刻を確認できる

### Phase 5 — ワークフロー（React Flow）

### Phase 5b — ワークフロー統合開発環境（2026-07-19 追加）

既存 Phase 5 の実行基盤を維持しながら、入力 → preview/test → node 入出力検査 → 部分再実行 → 公開を一体化する。
監査結果、データモデル、API、後方互換、Phase/PR 分割は
[`design-workflow-integrated-ide.md`](design-workflow-integrated-ide.md) を正とする。

1. UX 基盤: Preview Workspace、統一 inspector、debug panel、live canvas、過去入力 load
2. 再現性: published version、execution snapshot、node run、test case、pin、retry/resume、event stream
3. typed output / node / error route: output.render、approval/merge/try、data nodes、system trigger
4. large flow: group/collapse/subflow/outline/layout/performance
5. AI: diagnose/patch/runtime route/Project Intelligence
6. sample/docs: 15 以上の実用 sample、全 node 詳細説明、回帰 E2E

mock による決定的回帰に加え、LLM/RAG/AI 関連は利用可能なローカル model を必要に応じて実行し、
品質、token、latency、cancel/timeout、fallback、VRAM route まで評価する。
### Phase 6 — リモートデスクトップ（Guacamole）
### Phase 7 — TOTP / 通知 / バックアップ / PostgreSQL / PWA / WoL / プラグイン
- LLM runtime providerカタログ（Ollama / llama.cpp / LM Studio / OpenAI互換）と共通エンドポイント検出
- capability付きモデルadapter（共通一覧 / ロード / アンロード / 削除。未対応操作は明示的に拒否）

## 環境構築方針（ユーザー追加要求）

- Python 依存はリポジトリ直下 `.venv/` に閉じ込め、**起動スクリプトが存在しなければ自動構築**する
- Node 依存・ビルド・linger 設定などは `scripts/setup.sh` が一括実施
- systemd サービス登録は `scripts/install-service.sh`（ユーザーサービス、root 不要）

## 各 Phase の完了手順（要求仕様 §33）

リポジトリ調査 → 計画 → 実装（PC + iPhone 同時）→ 単体テスト → Ubuntu 実起動 → API 確認 →
PC ブラウザ確認 → モバイル幅確認 → systemd 継続確認 → SSH 切断継続確認 → 修正 → status 文書更新。
