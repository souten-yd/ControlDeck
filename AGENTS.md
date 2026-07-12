# AGENTS.md — 開発エージェント向けガイド

このリポジトリで作業する AI エージェント / 開発者向けの規約。

## プロジェクト概要

Ubuntu Control Deck — Ubuntu PC を Web から一元管理するセルフホストアプリ。
要求仕様の原本は `docs/requirements.md`。実装は Phase 単位（`docs/implementation-plan.md`）。
現在の進捗は必ず `docs/implementation-status.md` を確認・更新すること。

## 技術スタック

- Backend: Python 3.11+ / FastAPI / Uvicorn / SQLAlchemy 2.x / Pydantic v2 / psutil / SQLite（将来 PostgreSQL）
- Frontend: React / TypeScript / Vite / React Router / TanStack Query / Zustand / Tailwind CSS
- プロセス実行: systemd ユーザーユニット（`systemctl --user`）。Web プロセスの子プロセス化は禁止。

## 起動・テスト

```bash
./deck.sh          # 自動セットアップ + 起動（唯一のエントリースクリプト。サービス登録済みなら再起動）
./deck.sh test     # バックエンドテスト（= cd backend && ../.venv/bin/python -m pytest）
./deck.sh service  # systemd ユーザーサービス登録
cd frontend && npm run build   # フロントエンドのみビルド
```

scripts/ 配下は deck.sh への互換ラッパーのみ。新しい運用操作は deck.sh にサブコマンドとして追加する。

- Python venv はリポジトリ直下 `.venv/`。起動スクリプトが自動構築する。手動で `pip install` する場合も必ず venv 内で行う。
- フロントエンドのビルド成果物は `frontend/dist/`。バックエンドが静的配信する。

## 絶対に守るルール

1. **root で動くコードを書かない**。特権操作（再起動等）は helper 経由の設計を守る。
2. **`shell=True` 禁止**。subprocess は必ず配列引数。ユーザー入力をシェル文字列へ連結しない。
3. **パスは `Path.resolve()`（realpath 相当）で正規化**し、許可ルート配下か検証してから使う。
4. **秘密値（パスワード、トークン、TOTP シークレット等）をログへ出力しない**。
5. **API・WebSocket ごとに認証と権限を確認**する。デコレーター / 依存性で強制する。
6. **破壊的・重要操作は監査ログ**（`audit` サービス）へ記録する。
7. GPU 監視などセンサー取得の失敗でアプリ全体を落とさない。取得不可は `N/A`。
8. エラーを握り潰さない。ユーザー向けメッセージと内部ログを分離する。

## UI ルール

- 主要機能へ 2 ステップ以内。全操作ボタンの常時表示禁止（主操作 1 個 + その他メニュー）。
- モバイル（320〜767px）はボトムシート + 下部ナビ。Safe Area（`env(safe-area-inset-*)`）対応。
- 破壊的操作のみ確認ダイアログ。完了通知はトースト。赤色は破壊的操作と重大エラー限定。
- Monaco / React Flow / xterm.js / チャートは遅延ロード。大量リストは仮想化。
- ダーク / ライト両対応。等幅数字（`font-variant-numeric: tabular-nums`）。

## コード規約

- Backend: 型ヒント必須。Pydantic スキーマは `app/schemas/`、モデルは `app/models/`。
- Frontend: 関数コンポーネント + hooks。API 呼び出しは `src/api/` に集約し TanStack Query 経由。
- コミットは Phase / 機能単位で日本語 or 英語の簡潔なメッセージ。

## 完了の定義

コードを書くだけでは完了ではない。Ubuntu 上で実起動し、API 確認、PC ブラウザ幅とモバイル幅（320px 含む）の
両方で動作確認し、`docs/implementation-status.md` を更新してから完了とする。
