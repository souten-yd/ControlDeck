# Ubuntu Control Deck

Ubuntu PC を Web ブラウザ（PC / iPhone）から一元管理するセルフホスト型コントロールセンター。

スマホひとつで、サーバー PC のアプリ起動・リモートデスクトップ・ターミナル・ファイル管理・
ワークフロー自動化・GitHub リポジトリ運用までを完結させることを目指している。

## 主な機能

### 📊 ダッシュボード / 監視
- CPU / RAM / GPU / VRAM / 温度 / ディスク / ネットワークのリアルタイム監視（WebSocket 配信、AMD GPU は amd-smi 対応）
- しきい値アラート（継続時間・クールダウン設定、Discord / Slack / Webhook 通知）
- 1 分平均の履歴グラフ、システム自己診断、systemd ウォッチドッグ連携

### 📦 アプリ管理
- Python スクリプト / シェルスクリプト / 実行ファイル / 既存 systemd サービス / URL を登録し、起動・停止・再起動・ログ閲覧をワンタップで
- インラインコード編集（Monaco）+ **ストリーミング動作確認**（実行中の出力をリアルタイム表示、常駐アプリは停止ボタンで終了）
- **Web ボタン**: 待受ポートを自動検出し、サーバーアプリを全画面ビューアで開く（複数ポートは初回選択→記憶）
- パス入力はサーバー内ファイル選択ダイアログ対応。実行時のカレントは既定でホーム
- systemd ユーザーユニットとして実行されるため、ブラウザや SSH を切断しても動き続ける

### 🖥 リモートデスクトップ
- guacd + xrdp によるブラウザからのデスクトップ操作（ヘッドレス仮想セッション対応、`./deck.sh enable-desktop` で有効化）
- iPhone 向けタッチパッド操作: 1本指移動=カーソル / タップ=クリック / 長押し→移動=ドラッグ / 2本指タップ=右クリック / 2本指上下=スクロール / 3本指タップ=キーボード
- タッチ端末は 2 倍解像度で接続し縮小表示（ウィンドウのはみ出し防止）。RDP / VNC / SSH の外部ホスト接続も登録可能

### ⌨ Web ターミナル
- tmux による永続セッション（ブラウザを閉じても、バックエンドを再起動しても継続）
- 自動再接続、モバイル補助キーバー（Esc / Tab / Ctrl / 矢印 / ^C…）、コピー / 貼り付けシート

### 🔀 ワークフロー自動化（Dify / n8n 風）
- React Flow ベースのビジュアルエディタ。30 種類のノード:
  アプリ制御 / 条件分岐 / ループ / 変数 / 文字列操作 / ファイル入出力 / HTTP / ダウンロード /
  スクレイピング / ブラウザ操作（Playwright）/ LLM / RAG / OCR / DB クエリ / SSH / Git / C++ ビルド / WOL / Webhook 通知 / 現在日時 など
- **トリガー入力フィールド**: テキスト / 長文 / 数値 / 選択 / ファイルの型付き入力を定義し、実行時ダイアログで受け取る
- **変数ピッカー**: 上流ノード（直前に限らず全経路）の出力を一覧から選んで `{{ノードID.フィールド}}` を挿入。「出力変数名」を付ければ `{{vars.名前}}` でも参照可能
- **LLM ノード**: 稼働中の OpenAI 互換サーバー（Ollama / llama.cpp / LM Studio / vLLM 等）を自動検出してワンタップ設定。構造化出力（JSON スキーマ + プリセット、非対応サーバーへは自動フォールバック）
- スケジュール実行（間隔 / 毎日 / cron）、チャットフロー（チャット入力→信号表示ノードで応答）

### 🐙 GitHub 管理
- リポジトリ URL を登録するだけで `~/ControlDeckApps` へクローン
- 更新（pull）/ 保存（commit）/ リバート（履歴から時点選択）/ 削除 をボタンで操作
- 非公開リポジトリは「GitHub にログイン」（gh のデバイスフロー）でブラウザ認証

### 📁 ファイル / その他
- ファイルマネージャー（閲覧 / アップロード / テキスト編集 / プレビュー。許可ルート + 拒否リストで保護）
- ログ管理（アプリ別ストリーム表示・ローテーション）、監査ログ、RBAC（管理者 / 操作者 / 閲覧者）
- TOTP 二要素認証、PWA（ホーム画面追加）、ダーク / ライトテーマ、バックアップ / リストア

## 必要環境

- Ubuntu 24.04 以降（systemd ユーザーセッション必須。22.04 は Python / Node が古く不可）
- Python 3.11+ / Node.js 18+（無ければ `./deck.sh` が対話的に apt 導入を提案）

## セットアップ・起動

触るスクリプトは **`./deck.sh` 1 つだけ**。素の Ubuntu でも、不足要素
（apt パッケージ / venv / Node 依存 / フロントエンドビルド / 設定 / linger / 管理者）を対話形式で自動的に整える。

```bash
git clone https://github.com/souten-yd/ControlDeck.git && cd ControlDeck
./deck.sh              # 自動セットアップ → 起動（初回は管理者作成を対話で促す）
```

```bash
./deck.sh service        # systemd ユーザーサービスとして登録（OS 起動時に自動起動）
./deck.sh status         # 状態確認
./deck.sh stop           # サービス停止
./deck.sh admin <名前>    # 管理者を追加
./deck.sh passwd <名前>   # パスワード変更
./deck.sh reset-totp <名前> # 二要素認証の解除（ロックアウト復旧）
./deck.sh backup         # DB / 設定 / ユニットのバックアップ
./deck.sh restore <file> # 復元
./deck.sh enable-desktop # この PC のリモートデスクトップを有効化（ヘッドレス）
./deck.sh test           # バックエンドテスト
```

サービス登録済みの状態で `./deck.sh` を実行すると、変更を反映してサービスを再起動する。
コード変更後の反映は `systemctl --user restart control-deck-web`（Python はこれだけ、
フロントは `cd frontend && npm run build` してから）。

デフォルトでは `http://127.0.0.1:8765` で待ち受ける。スマホから使う場合は
`config/config.yaml` の `server.host` を変更して Tailscale / WireGuard 経由でのアクセスを推奨
（`files.allowed_roots` などの設定も同ファイル。例は `config/config.example.yaml`）。

## 構成

| ディレクトリ | 内容 |
|---|---|
| `backend/` | FastAPI バックエンド（認証 / アプリ / 監視 / ファイル / ターミナル / ワークフロー / リモート / GitHub） |
| `frontend/` | React + TypeScript + Vite フロントエンド（PWA） |
| `scripts/` | 補助スクリプト（deck.sh の互換ラッパー等） |
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

- Web サービスは root で動作させない（systemd ユーザーサービス + linger）
- Web から起動したアプリは systemd ユーザーユニットで実行し、ブラウザ / SSH 切断後も継続する
- 任意コマンド実行は初期無効（`security.allow_arbitrary_commands`）
- ファイルアクセスは許可ルート配下のみ（realpath 正規化 + symlink 脱出防止、`~/.ssh` 等は常時拒否）
- サブプロセスはすべて配列引数（shell=False）、Cookie セッション + CSRF ヘッダー + Origin 検証
- 重要操作はすべて監査ログへ記録

詳細は [docs/security-model.md](docs/security-model.md) を参照。

## ライセンス

MIT
