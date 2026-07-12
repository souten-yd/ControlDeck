# アーキテクチャ

## 全体構成

```text
Browser (PC / iPhone)
   │ HTTP(S) + WebSocket
   ▼
control-deck-web  (FastAPI + Uvicorn, 一般ユーザー権限)
   ├── api/          REST + WebSocket エンドポイント (/api/v1)
   ├── auth/         セッション認証・RBAC・TOTP(将来)
   ├── applications/ アプリ登録・systemd ユーザーユニット生成・状態取得
   ├── monitoring/   psutil + GPU プロバイダー (amd-smi → rocm-smi → sysfs / nvidia-smi)
   ├── logs/         アプリ stdout/stderr の tail・ストリーム・削除
   ├── audit/        監査ログ
   ├── files/        (Phase 4) 許可ルート限定ファイル操作
   ├── terminals/    (Phase 4) tmux + PTY ゲートウェイ
   ├── workflows/    (Phase 5) ワークフローエンジン
   └── database/     SQLAlchemy 2.x + SQLite (WAL)
   │
   ▼ (特権操作のみ)
control-deck-helper (Phase 電源管理時に導入: systemd 経由の reboot/shutdown)
```

MVP 段階では web プロセスに agent 機能（メトリクス収集・状態監視）を同居させ、
ワークフロー / 高負荷収集が入る Phase で `control-deck-agent` に分離する。

## プロセス継続性の設計

Web から起動するアプリは **systemd ユーザーユニット** として実行する。

- ユニット名: `cdapp-{app_id}.service`
- ユニットファイル: `~/.config/systemd/user/cdapp-{app_id}.service`（安全なテンプレートから生成、値はすべて検証済み）
- stdout/stderr: `StandardOutput=append:` で `{data_dir}/logs/{app_id}/stdout.log` / `stderr.log` へ
- 起動/停止/再起動: `systemctl --user start|stop|restart cdapp-{id}`（配列引数、shell=False）
- 状態取得: `systemctl --user show` の `ActiveState` / `SubState` / `MainPID` / `ExecMainStatus` を
  アプリ状態（RUNNING / STOPPED / FAILED / STARTING / ...）へマッピング
- ログイン外でも動かすため `loginctl enable-linger` をセットアップ時に実施

これにより Web バックエンド再起動・SSH 切断・ブラウザ終了の影響を受けない。
Web は「ユニットの操作と観測」だけを行い、プロセスを保有しない。

## 認証・セッション

- パスワード: Argon2id（argon2-cffi）
- セッション: ランダムトークン（sha256 ハッシュを DB 保存）+ HttpOnly / SameSite=Lax Cookie。サーバー側で失効管理。
- CSRF: SameSite=Lax + 状態変更 API はカスタムヘッダー `X-Requested-With` 要求（Cookie 認証のみ許可のため）
- RBAC: Role（administrator / operator / viewer）→ 権限セット。API 依存性 `require_permission()` で強制。
  WebSocket も接続時に同一の検証を行う。
- ログインはレート制限（IP + ユーザー名単位、指数バックオフ）。

## メトリクス

- 収集: バックグラウンド asyncio タスクが 2 秒間隔で psutil / GPU プロバイダーから収集し、
  リングバッファ（24h 生データは SQLite の metrics テーブルへ 1 分平均で保存）
- 配信: 単一 WebSocket `/api/v1/system/metrics/stream` へ集約。REST スナップショット `/api/v1/system/overview`
- GPU: プロバイダー抽象化（AmdSmiProvider / RocmSmiProvider / SysfsAmdProvider / NvidiaSmiProvider / NullProvider）。
  起動時に自動検出、失敗時は N/A を返しシステム全体は継続。

## データベース

SQLite（WAL モード、`{data_dir}/control-deck.db`）。SQLAlchemy 2.x の同期エンジン +
FastAPI スレッドプール実行。接続文字列は設定で PostgreSQL へ差し替え可能な構造。

## ディレクトリ

- 設定: `config/config.yaml`（リポジトリ実行時）または `~/.config/control-deck/config.yaml`
- データ: `~/.local/share/control-deck/`（DB、ログ、アイコン）
- フロントエンド: ビルド済み `frontend/dist` を FastAPI が静的配信（SPA fallback）
