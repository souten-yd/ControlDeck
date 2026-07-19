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
- React Flow ベースのビジュアルエディタ。標準 40 種類 + OpenCode 有効時の `code.agent` ノード:
  アプリ制御 / 条件分岐 / ループ / 変数 / 文字列操作 / ファイル入出力 / HTTP / ダウンロード /
  スクレイピング / ブラウザ操作（Playwright）/ LLM / RAG / Deep Research / OCR / DB クエリ / SSH / Git / C++ ビルド / WOL / Webhook 通知 / 現在日時 など
- **トリガー入力フィールド**: テキスト / 長文 / 数値 / 選択 / ファイルの型付き入力を定義し、実行時ダイアログで受け取る
- **変数ピッカー**: 上流ノード（直前に限らず全経路）の出力を一覧から選んで `{{ノードID.フィールド}}` を挿入。「出力変数名」を付ければ `{{vars.名前}}` でも参照可能
- **LLM ノード**: 稼働中の OpenAI 互換サーバー（Ollama / llama.cpp / LM Studio / vLLM 等）を自動検出してワンタップ設定。構造化出力（JSON スキーマ + プリセット、非対応サーバーへは自動フォールバック）
- **安全プレビュー**: executor を呼ばず、実行順・分岐・予定されるファイル書込や外部通信・必要 capability を事前確認
- **実行プレビュー**: 入力、予定副作用、安全プレビュー、通常テスト、型付き最終出力、ノード結果、過去入力の再利用を同じ画面で確認
- **AI 生成の検証**: 生成したフローを構造・意味の両面で検証し、品質スコアと修正点を表示してから登録
- **ノードカタログ**: 検索、カテゴリ、localStorage お気に入り、利用可能ノード絞り込み、未導入機能の案内。実行中は対応ノードの進捗を表示
- JSON / CSV 変換、Schema 検証、許可ルート内 glob、embedding / rerank / LLM judge、分離 context による並列 map に対応
- Web スクレイピングは sandbox iframe 上で要素を選び、CSS selector を作成して複数項目を名前付き出力可能
- RAG は6種類のチャンク戦略、vector / full-text / hybrid / graph 検索、HyDE / multi-query、Web・学術検索から引用付きレポートを作る Deep Research に対応
- スケジュール実行（間隔 / 毎日 / cron）、チャットフロー（チャット入力→信号表示ノードで応答）

### 🤖 AI アシスタント / LLM Model
- サーバー側に会話を保存する全画面 AI アシスタント。チャット、ワークフロー生成、会話の改名・削除に対応し、ブラウザを閉じてもジョブを継続
- Ollama / llama.cpp / 外部 OpenAI 互換 provider を共通画面で検出・管理。llama.cpp は複数 GGUF、モデル別 systemd user unit、個別の Context / K/V cache / GPU offload / MTP / MoE / sampling 設定に対応（CTX は 256K preset、出力上限は最大 131K token を選択可能）
- 生成 stream・thinking・usage・cancel を provider 間で共通化。AMD GPU では対応環境に限り静音 / バランス / フルパワー / カスタムの電力・クロックプロファイルを利用可能
- Knowledge / RAG 管理、hybrid / vector / full-text / GraphRAG、HyDE / multi-query、Web・学術検索を組み合わせた Deep Research

### 🧑‍💻 OpenCode（オプション）
- OpenCode coding agent を Control Deck の独立画面とワークフロー `code.agent` ノードから利用
- 分析 / 実装 / 不具合修正 / テスト / レビューを、指定プロジェクトと OpenAI 互換ローカル LLM に対して実行
- 通常セットアップには含まれない明示的なオプトイン機能。有効時だけ API、画面、メニュー、ワークフローノードを登録
- 実行は systemd user transient unit に分離し、キャンセル、出力上限、許可ルートと symlink 境界の検証に対応

### 🐙 GitHub 管理
- リポジトリ URL を登録するだけで `~/ControlDeckApps` へクローン
- 更新（pull）/ 保存（commit）/ リバート（履歴から時点選択）/ 削除 をボタンで操作
- 非公開リポジトリは「GitHub にログイン」（gh のデバイスフロー）でブラウザ認証

### 📁 ファイル / その他
- ファイルマネージャー（閲覧 / 再開可能アップロード / ごみ箱 / テキスト編集 / プレビュー。許可ルート + 拒否リストで保護）
- ログ管理（アプリ別ストリーム表示・ローテーション）、監査ログ、RBAC（管理者 / 操作者 / 閲覧者）
- TOTP 二要素認証、PWA（ホーム画面追加）、ダーク / ライトテーマ、バックアップ / リストア

## 最近の主な追加（2026-07-15〜17）

- OpenCode を既定無効のオプトイン feature として統合
- 独立 AI アシスタント、永続会話、ジョブの優先度・進捗 stream・cancel を追加
- llama.cpp の複数 GGUF 管理と Ollama / 外部 OpenAI 互換 provider の共通モデル操作を追加
- ワークフローを標準39ノードへ拡張し、生成時の意味検証・品質スコア、安全プレビュー、並列 map、型・side effect metadata、検索・お気に入りを追加
- アプリアイコン、TCP / HTTP / ファイルのヘルスチェック、ごみ箱、再開可能アップロード、永続電源予約を追加
- AMD GPU 監視を sysfs fast path へ移行し、Web ポーリングとジョブ通知を軽量化
- AI アシスタントと Web ターミナルの 320px / iOS 向け入力・再接続・履歴復元を改善
- ワークフロー生成を標準 JSON Schema payloadへ修正し、固定800 tokenではなくModel画面の共通出力上限を使用

詳細と検証結果は [実装状況](docs/implementation-status.md) を参照。

## ワークフローの基本的な使い方

ControlDeckのエディタは「配置して保存」で終わらず、入力→安全確認→テスト→観測→部分再実行→回帰テスト→公開を
1つの開発ループとして扱う。

1. ワークフローを作り、トリガーノードで入力名・型・必須・初期値・説明を定義する。
2. ノードを接続し、設定欄の変数ピッカーから`{{上流ノードID.フィールド}}`を挿入する。
3. 「プレビュー」を開き、入力値を設定して「安全プレビュー」を実行する。この段階ではexecutor、LLM、外部通信、書込み、secret復号を行わない。
4. 通常テストへ切り替え、同じ画面で型付き最終出力と各ノードの結果を確認する。
5. ノードの「実行」タブで、最新成功実行・指定実行・手動JSON・固定データを入力源にして単体実行する。上流を再計算せずに問題を切り分けられる。
6. 必要なら「このノードまで実行」「このノードから再実行」を使う。途中再実行は当時版／現在版を選択できる。
7. 入力と成功結果を回帰テストケースとして保存し、変更後に一括実行する。期待値との差はpath・期待値・実値で表示される。
8. 「公開」でpreflightを通す。保存中draftと公開版は分離され、schedule、Webhook、system event、API実行、サブフローは公開版だけを使う。

### 型付き最終出力 `output.render`

新規フローの最終段には`output.render`を推奨する。旧`signal.display`は既存定義との互換用として継続利用できる。
出力名、タイトル、説明、値、renderer、schema、ファイル名、MIME type、コピー／ダウンロード／折り畳み／機密指定を持ち、
手動実行・API・schedule・chat・サブフローで共通の`name / type / value / source_node_id`契約を返す。

主なrendererはAuto、Plain text、Markdown、JSON tree/raw、Table、Key-value、Code、Image/Gallery、Audio、Video、
File、Link、Status、Metric、Progress、Citation list。JSON・Table・Key-value等はJSON文字列を型付き値へ変換する。
`sensitive`を有効にした値は実行中の後段参照には使えるが、履歴・DB・API応答へ保存するときに`***`へ置換される。

### draft、公開版、固定データの違い

- draft: エディタで自動保存される開発中定義。Previewの通常テスト、単体実行、回帰テストが使用する。
- 公開版: 公開時点のimmutableなWorkflowVersion。本番経路が使用し、その後draftを編集しても変化しない。
- 固定データ: 下流テスト用にノード出力を一時固定する開発補助。公開版には含まれず、残っている場合は公開を停止する。

公開時は構造・意味、最終出力、出力名重複、secret存在、固定データ、回帰テスト状態、品質スコアを検査する。
blocking errorがある場合はスコアが高くても公開しない。

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
./deck.sh disable-desktop # この PC のリモートデスクトップを無効化
./deck.sh searxng        # Deep Research 用 SearXNG を導入・アプリ登録
./deck.sh test           # バックエンドテスト
```

サービス登録済みの状態で `./deck.sh` を実行すると、変更を反映してサービスを再起動する。
コード変更後の反映は `systemctl --user restart control-deck-web`（Python はこれだけ、
フロントは `cd frontend && npm run build` してから）。

デフォルトでは `http://127.0.0.1:8765` で待ち受ける。スマホから使う場合は
`config/config.yaml` の `server.host` を変更して Tailscale / WireGuard 経由でのアクセスを推奨
（`files.allowed_roots` などの設定も同ファイル。例は `config/config.example.yaml`）。

## OpenCode の導入と使い方

OpenCode は通常の `./deck.sh` ではインストールも有効化もされない。Node.js / npm と、Ollama、
llama.cpp、LM Studio などの OpenAI 互換 LLM endpoint を用意したうえで、次の順に操作する。

```bash
./deck.sh feature status opencode   # 管理導入・PATH 上の既存 OpenCode・有効状態を確認
./deck.sh feature install opencode  # Control Deck の data directory 専用 prefix へ導入
./deck.sh feature enable opencode   # 明示的に有効化
```

サービスが稼働中なら `install` / `enable` 後に `deck.sh` が自動で Web サービスを再起動する。
サービス化していない場合は、続けて `./deck.sh` を実行し直す。有効化後、PC のサイドバーまたは
コマンドパレットから「OpenCode」を開く。モバイルでは `/opencode` を直接開き、次を設定する。

1. LLM endpoint: OpenAI 互換 API のベース URL（例: Ollama は `http://127.0.0.1:11434/v1`）
2. モデル: endpoint が公開するモデル ID
3. プロジェクト: `config/config.yaml` の `files.allowed_roots` 配下にあるディレクトリ
4. 操作と指示: `分析` / `実装` / `不具合修正` / `テスト` / `レビュー` から選択して実行

既に `opencode` が PATH 上にあれば `install` は不要で、そのまま `enable` できる。状態を戻す場合は次を使う。

```bash
./deck.sh feature disable opencode   # データを残して UI / API / code.agent を無効化
./deck.sh feature uninstall opencode # 無効化し、Control Deck 管理 prefix の導入物だけ削除
```

`uninstall` は PATH 上の外部 OpenCode やユーザーの OpenCode 設定・データを削除しない。
`implement` / `fix` は対象プロジェクトを書き換え得るため、実行前に Git で作業内容を保存することを推奨する。
設計上の境界と詳細は [OpenCode オプトイン feature 詳細設計](docs/design-opencode-feature.md) を参照。

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
- [OpenCode オプトイン feature 詳細設計](docs/design-opencode-feature.md)
- [ワークフロー dry-run / metadata 詳細設計](docs/design-workflow-dry-run-metadata.md)
- [ワークフローノード catalog 詳細設計](docs/design-workflow-node-catalog.md)
- [ワークフロー統合開発環境 監査・詳細実装仕様](docs/design-workflow-integrated-ide.md)

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
