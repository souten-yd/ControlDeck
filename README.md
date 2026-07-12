# Ubuntu Control Deck

Ubuntu PC を Web ブラウザ（PC / iPhone）から一元管理するセルフホスト型コントロールセンター。

Python アプリ・シェルスクリプトの登録と systemd による永続実行、システム監視（CPU / RAM / GPU / VRAM）、
ログ管理、監査ログ、レスポンシブ UI を提供する。

## 必要環境

- Ubuntu 24.04 系（systemd ユーザーセッション必須）
- Python 3.11+（開発環境は 3.12 で確認）
- Node.js 20+（フロントエンドのビルド時のみ）

## セットアップ

```bash
# 初回セットアップ（venv 構築 + 依存インストール + フロントエンドビルド + 初期管理者作成）
./scripts/setup.sh

# 開発起動（venv がなければ自動構築して起動）
./scripts/run-dev.sh

# systemd サービスとして登録（ユーザーサービス、Ubuntu 起動時に自動起動）
./scripts/install-service.sh
```

初回起動時に管理者アカウントを作成する:

```bash
./scripts/create-admin.sh <username>
```

デフォルトでは `http://127.0.0.1:8765` で待ち受ける。LAN 公開する場合は
`config/config.yaml` の `server.host` を変更する（警告あり。Tailscale / WireGuard / リバースプロキシ + HTTPS を推奨）。

## 構成

| ディレクトリ | 内容 |
|---|---|
| `backend/` | FastAPI バックエンド（API / 認証 / アプリ管理 / 監視 / ログ / 監査） |
| `frontend/` | React + TypeScript + Vite フロントエンド |
| `scripts/` | セットアップ・起動・サービス登録スクリプト |
| `deploy/` | systemd ユニット、リバースプロキシ設定例 |
| `docs/` | 要求仕様・設計・実装状況ドキュメント |

## ドキュメント

- [要求仕様（原本）](docs/requirements.md)
- [アーキテクチャ](docs/architecture.md)
- [セキュリティモデル](docs/security-model.md)
- [UI/UX ガイドライン](docs/ui-ux-guidelines.md)
- [モバイルレイアウト](docs/mobile-layout.md)
- [実装計画](docs/implementation-plan.md)
- [実装状況](docs/implementation-status.md)

## セキュリティ原則（抜粋）

- Web サービスは root で動作させない
- Web から起動したアプリは systemd ユーザーユニットで実行し、ブラウザ / SSH 切断後も継続する
- 任意コマンド実行は初期無効
- ファイルアクセスは許可ルート配下のみ（realpath 正規化 + symlink 脱出防止）
- 重要操作はすべて監査ログへ記録

詳細は [docs/security-model.md](docs/security-model.md) を参照。

## ライセンス

MIT
