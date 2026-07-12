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

### Phase 3 — 監視
- CPU / RAM / ディスク / ネットワーク / 稼働時間（psutil）
- GPU / VRAM（amd-smi → rocm-smi → sysfs → nvidia-smi、失敗時 N/A）
- 消費電力推定、単一メトリクス WS ストリーム、履歴（生 24h / 1 分平均 30 日）
- アラート（しきい値 + 通知センター）

### Phase 4 — ファイル + ターミナル
- 許可ルート限定ファイルマネージャー（一覧 / アップロード / ダウンロード / 編集 / コピー / 移動 / ごみ箱）
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
### Phase 6 — リモートデスクトップ（Guacamole）
### Phase 7 — TOTP / 通知 / バックアップ / PostgreSQL / PWA / WoL / プラグイン

## 環境構築方針（ユーザー追加要求）

- Python 依存はリポジトリ直下 `.venv/` に閉じ込め、**起動スクリプトが存在しなければ自動構築**する
- Node 依存・ビルド・linger 設定などは `scripts/setup.sh` が一括実施
- systemd サービス登録は `scripts/install-service.sh`（ユーザーサービス、root 不要）

## 各 Phase の完了手順（要求仕様 §33）

リポジトリ調査 → 計画 → 実装（PC + iPhone 同時）→ 単体テスト → Ubuntu 実起動 → API 確認 →
PC ブラウザ確認 → モバイル幅確認 → systemd 継続確認 → SSH 切断継続確認 → 修正 → status 文書更新。
