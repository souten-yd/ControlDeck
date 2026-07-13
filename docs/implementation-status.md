# 実装状況

最終更新: 2026-07-12

## サマリー

| Phase | 状態 |
|---|---|
| 文書整備 | ✅ 完了 |
| Phase 1 — 認証 + レイアウト | ✅ 完了 |
| Phase 2 — アプリ管理 | ✅ 完了（アイコンアップロードは未対応、頭文字アイコンで代替） |
| Phase 3 — 監視 | ✅ 完了（アラート通知を含む） |
| Phase 4 — ファイル + ターミナル | ✅ 完了（ごみ箱・チャンクアップロードは未対応） |
| Phase 5 — ワークフロー | ✅ コア完了（下記参照） |
| Phase 6 — リモートデスクトップ | ✅ コア完了（guacd トンネル + 接続管理 + ビューア） |
| Phase 7 — TOTP ほか | ✅ コア完了（TOTP/PWA/バックアップ。WoL はワークフローノードで対応） |

## リモートデスクトップの環境互換性メモ（2026-07-12、重要）

- **Control Deck 側は完全動作**: WS トンネル・認証・guacd ハンドシェイク・ビューアは実機で確認済み
  （guacd が接続を受理し ready/size/image/cursor を配信）
- **ブロッカー**: Ubuntu 24.04 同梱の guacd 1.3.0（FreeRDP 2.11.5）は GNOME Remote Desktop 46
  （FreeRDP 3 系）と RDP ネゴシエーション非互換（全 security タイプで "wrong security type"）
- **対処**: ヘッドレスは **xrdp**（FreeRDP2 互換）を使う方式へ変更。`enable-desktop`（既定ヘッドレス）は
  xrdp を導入し、システムアカウントで PAM 認証、接続時に新規セッションを作成。GNOME RD の RDP は解放
- **接続フォームに security 選択を追加**（any/nla/tls/rdp）。Windows は nla、xrdp は any
- 既知の注意: xrdp + GNOME は「同一ユーザーが同時に 1 セッションのみ」の制約あり。画面を閉じた
  ヘッドレス運用（コンソール未ログイン）を想定

## この PC のヘッドレスデスクトップ操作（2026-07-12、ユーザー要望）

- **`./deck.sh enable-desktop`**（既定ヘッドレス）: GNOME Remote Desktop を `grdctl --system` で設定し、
  この Ubuntu を Web から操作可能にする。TLS 証明書を openssl で自動生成、RDP 認証情報を対話入力、
  guacd を導入、Control Deck に `127.0.0.1:3389` への接続「この PC（headless）」を自動登録
- **ヘッドレス（既定）**: 接続時に仮想セッションを作成（物理画面不要、画面を閉じた運用向け）。
  **リモート接続を有効化するまで仮想デスクトップは作られない**（enable-desktop を実行し、かつ
  クライアントが接続したときのみ）
- **`--active`**: 現在のログインセッションを共有（画面ミラー）。`grdctl`（ユーザー daemon）
- **`./deck.sh disable-desktop`**: 無効化
- 接続登録は `app.cli register-local-desktop`（パスワードは環境変数経由で argv に載せない、暗号化保存）
- セキュリティ: RDP:3389 は Control Deck 経由での利用を前提。外部はファイアウォール/VPN で遮断を案内

注: enable-desktop はシステム状態変更（サービス有効化・ポート開放・パスワード設定）を伴うため、
ユーザーが明示実行する。アプリ側が勝手に仮想セッションを作ることはない。

## Phase 6 リモートデスクトップ（2026-07-12）

- **guacd トンネル**: WebSocket（guacamole-common-js）↔ guacd(TCP:4822) を橋渡し。接続開始時の
  ハンドシェイク（select → args → size/audio/video/image → connect）をサーバー側で実施し、
  以降は raw ストリームを双方向パイプ（guacamole-lite 相当を Python で実装、外部依存なし）
- **接続管理**: RDP / VNC / SSH の接続 CRUD。パスワード等の機微パラメータは Fernet 暗号化保存、
  API 応答には含めない（has_password フラグのみ）。RDP は ignore-cert / display-update を既定化
- **ビューア**: guacamole-common-js（遅延ロード）。マウス + タッチパッド（タップ=クリック・長押し=右クリック）+
  キーボード、Ctrl+Alt+Del、画面リサイズ追従、モバイルはソフトキーボード呼び出し
- **導入**: `remote_desktop.enabled: true` のとき deck.sh が guacd の apt 導入を試みる。
  未導入時は UI に案内を表示し接続ボタンを無効化
- **バックアップ修正**: sqlite3 CLI 非依存に変更（venv Python の sqlite3 backup API で整合スナップショット）

検証: pytest 79 件成功（命令エンコード/パーサ、モック guacd での select→args→connect ハンドシェイク、
接続 CRUD、パスワード暗号化非漏洩）。Playwright で接続一覧・追加フォームを PC/モバイル確認。
ライブ接続は guacd + 実ホストが必要なためこの環境では未実施。

## バックアップ / リストア（2026-07-12、Phase 7）

- `./deck.sh backup [出力先]`: DB / 設定 / 暗号鍵 / RAG / アプリの systemd ユニットを tar.gz に。
  sqlite3 があれば WAL checkpoint 後にコピー（ログは容量のため既定除外）
- `./deck.sh restore <ファイル>`: 復元前に自動退避コピー、確認プロンプトつき、daemon-reload
- `GET /system/backup`（settings.manage）: 設定ページの「バックアップ」からブラウザで DL 可能
- 検証: backup→DB 改変→restore で復旧＋退避コピー生成を確認。DL API も 200/gzip 確認

## PWA 対応（2026-07-12、Phase 7）

- manifest.webmanifest（standalone、テーマ色、192/512/maskable アイコン）+ apple-touch-icon /
  apple-mobile-web-app メタ。ホーム画面追加・フルスクリーン起動に対応
- Service Worker（sw.js）: **アプリシェル（HTML/JS/CSS/アイコン）のみキャッシュ**。
  `/api/`・WebSocket・認証は一切キャッシュしない（機密を Service Worker に保存しない方針）。
  アセットは cache-first（ハッシュ付き名）、ナビゲーションは network-first + オフライン時シェルフォールバック
- 本番ビルドのみ SW 登録（開発時は登録しない）。アイコンは Chromium で SVG ロゴから生成
- TOTP リセット: `./deck.sh reset-totp <ユーザー名>`（`--all` で全員）でロックアウト復旧可能

検証: SW 登録・アクティブ化・manifest 読込を Playwright で確認。オフライン再読み込みでアプリシェルが
起動することを確認。

## TOTP 二要素認証（2026-07-12、Phase 7）

- 有効化: setup（QR=SVG data URI、Pillow 不要）→ 6 桁 verify → リカバリーコード 10 個を 1 回表示
- ログイン 2 段階: TOTP 有効時は `two_factor_required` → コード入力（6 桁 or 使い捨てリカバリー）
- 無効化はコード確認つき。シークレット/リカバリーコードは Fernet 暗号化保存、使用時に消費
- `require_totp_for_admin` で管理者に推奨バナー。bootstrap に SQLite 軽量マイグレーション追加
- 検証: pytest 71 件、Playwright + pyotp で全フロー E2E

## アラート通知（2026-07-12、Phase 3 残り完了）

- **ルール**: メトリクス（CPU/RAM/GPU/VRAM/CPU温度/GPU温度/ディスク使用率/アプリ停止）× 演算子（>/≥/</≤）
  × しきい値 × 継続時間（sustained）× クールダウン。アプリ停止は対象アプリ指定
- **通知チャンネル**: Discord / Slack / 汎用 Webhook。URL は Fernet 暗号化保存・表示時マスク・テスト送信可
- **評価ループ**: 15 秒間隔で評価。継続時間を満たすと AlertEvent 発火＋通知、条件解消で resolved。
  ウォッチドッグの心拍対象にも追加
- **UI**: 設定に通知チャンネル / アラートルール管理、ダッシュボードにアクティブアラートバナー
- **必要ソフトの自動導入**: deck.sh に tesseract / tmux の apt 導入（passwordless sudo 時）と
  Playwright ブラウザ（Chromium）自動導入を追加

検証: pytest 62 件成功。E2E で webhook 受信サーバーへの発火通知＋テスト送信を確認。

## ワークフロー拡張 v2（2026-07-12、ユーザー要望）

- **ノード追加**（全 25 種）: ループ（回数 / foreach、body/done 2 出力、`{{ID.item}}`/`{{ID.index}}` 参照）、
  変数セット、文字列操作（大小変換 / 置換 / 正規表現抽出 / 分割 / JSON 抽出 / テンプレート）、
  Markdown→HTML、ファイル読込 / 出力（追記可）/ 操作（copy/move/delete/mkdir）、
  LLM 生成（OpenAI 互換 = Ollama/vLLM/llama.cpp/OpenAI）、Web スクレイピング（CSS セレクター）、
  ブラウザ操作（Playwright）、OCR（tesseract）、Wake-on-LAN、
  SSH 実行（鍵認証 BatchMode、host 検証）、Git 操作（サブコマンド許可制）、C++ ビルド（CMake/Make）、
  Python 実行（**初期無効**、`security.allow_arbitrary_commands` で許可、venv python の -I -c 実行）
- **安全性**: すべて shell=False の配列実行。ファイル系は許可ルート検証を通す。SSH host / Git サブコマンドは
  ホワイトリスト。任意シェル文字列ノードは非提供
- **エディター刷新**: アイコン付きノード + カテゴリカラーバー、実行状態リング、ドットグリッド背景、
  ミニマップ、矢印マーカーエッジ、ループの反復/完了ハンドル
- **カスタムノード / スニペット**: 選択ノード群をスニペットとして localStorage 保存 → パレットから再挿入
- **ワークフロー入出力**: 定義を JSON でエクスポート / インポート（他環境への持ち運び）

検証: pytest 56 件成功（v2 ノード 13 件: 文字列 / 変数チェーン / Markdown / ファイル IO / WOL /
Git 許可制 / SSH host 検証 / Python 無効 / ループ foreach・count / スクレイピング）。
E2E で「foreach ループ → 大文字化 → ファイル追記」を実行し APPLE/BANANA/CHERRY 出力を確認。
Playwright でダーク/ライト・PC/モバイルのエディターとパレットを確認、横スクロール 0・エラーなし。

### RAG 構築 / DB 操作ノード（2026-07-12 実装）

- **db.query**: SQLite（許可ルート配下のファイル）または任意 SQLAlchemy URL（PostgreSQL 等）へ SQL 実行。
  名前付きパラメータ（`:id`）でバインド。先頭が SELECT/INSERT/UPDATE/DELETE/CREATE 等の SQL のみ許可、
  SQLite パスは許可ルート検証。SELECT は最大 500 行を dict で返す
- **rag.build**: テキスト（またはファイル）をチャンク分割 → OpenAI 互換 `/v1/embeddings`（Ollama の
  nomic-embed-text 等）で埋め込み → コレクション別 SQLite（data_dir/rag/{name}.db）へ保存
- **rag.query**: 質問を埋め込み、numpy コサイン類似度で top-k チャンクを取得。`{{ID.context}}` を
  LLM ノードへ渡すことで RAG パイプラインを構成（依存はベクトル DB 不要、numpy のみ）

検証: pytest 69 件成功。E2E で DB クエリ（テーブル作成→挿入→カウント）、RAG（フェイク埋め込みで
build→query マッチ）を実機ワークフローで確認。

## 自己メンテナンス / ウォッチドッグ（2026-07-12、ユーザー要望で追加）

- **systemd ウォッチドッグ**: `Type=notify` + `WatchdogSec=30` + `NotifyAccess=main`。
  起動完了時に READY=1、内部ヘルスチェック（DB 接続 / メトリクス収集の鮮度 / スケジューラー心拍）が
  正常な間のみ 15 秒間隔で WATCHDOG=1 を送信。ハング・内部異常時は systemd が SIGABRT → 自動再起動
- **自己メンテナンスループ**（起動 5 分後 + 1 時間間隔）:
  ログローテーション（copytruncate + gzip、`rotate_size_mb`/`rotate_generations`/`retention_days`、
  仕様 §11.3 対応）/ 期限切れセッション purge / 監査ログ保持（`audit_retention_days` 既定 180 日）/
  SQLite WAL checkpoint + optimize / ディスク残量自己点検（10% 未満で警告）
- **自己状態 API/UI**: `GET /system/self-status` + システムページ「Control Deck 自己診断」セクション

検証: pytest 43 件成功（ローテーション世代管理 / purge / ヘルスチェック / sd_notify フォールバック）。
実機で SIGSTOP によるハング模擬 → 30 秒で `Watchdog timeout` → SIGABRT → 自動再起動 → 復旧を確認。

## Phase 5 実装内容（2026-07-12）

- **エンジン**: ノードグラフ実行（トリガー → 逐次 + 条件分岐）。ノード別タイムアウト、
  ステップ上限 / ループ防止、実行キャンセル、ノードごとの入出力・エラー・時刻を保存
- **ノード**: トリガー（手動 / 間隔 / 毎日 / cron）、アプリ起動・停止・再起動・状態取得、
  HTTP リクエスト（期待ステータス検証）、条件分岐（eq/ne/gt/lt/contains、真偽 2 分岐）、待機、
  Webhook 通知（汎用 / Discord / Slack）、ファイル存在確認（許可ルート検証を通す）。
  テンプレート `{{ノードID.フィールド}}` で前段出力を参照可能。**任意シェル実行ノードは提供しない**（§20.6 安全モード）
- **スケジューラー**: 30 秒間隔で有効ワークフローの間隔 / 毎日 / cron（croniter）トリガーを評価
- **API**: workflows CRUD / run / enable / disable、workflow-executions 一覧・詳細・cancel（すべて RBAC + 監査）
- **UI**: 一覧（実行ボタン + 前回結果 + スケジュールトグル）、React Flow エディター（遅延ロード、
  カスタムノード、条件ノードは真/偽 2 ハンドル、ノードパレット + 設定ボトムシート、モバイル対応）、
  実行履歴シート（ノードごとの出力 JSON 表示、実行中は自動更新）

### UI テーマ / ロゴ（同日、PR #4）

- モード（システム / ライト / ダーク）+ アクセント 6 色 + OLED 完全黒。localStorage 永続化
- スライダーモチーフの SVG ロゴ（アクセント色連動、favicon 含む）

検証: pytest 37 件成功（定義検証 / テンプレート / 条件分岐グラフ / API CRUD+実行 / viewer 権限 /
スケジュール判定）。E2E で HTTP ヘルスチェック → 条件分岐 → true 側のみ実行を確認。
Playwright（1280 / 390px）でエディター・パレット・設定シート・実行履歴を確認、横スクロール 0・エラーなし。

## Phase 4 実装内容（2026-07-12）

- **ファイル**: 許可ルート限定（realpath + commonpath + 拒否リスト ~/.ssh 等）。一覧 / アップロード
  （複数・D&D・上書き確認・サイズ上限）/ ダウンロード / プレビュー（画像）/ テキスト編集
  （Monaco 遅延ロード、CDN 不使用、Ctrl+S 保存）/ mkdir / rename / copy / move / 削除（確認 + 監査）
- **ターミナル**: PTY + WebSocket。tmux があれば永続セッション（cdterm-*）、なければプロセス内 PTY
  フォールバック（切断→再接続でバッファリプレイ）。xterm.js 遅延ロード、モバイル全画面 +
  補助キーバー（Esc/Tab/Ctrl/矢印/^C/^D）、visualViewport 対応、リサイズ同期、監査記録
- **運用**: 単一エントリースクリプト `./deck.sh` へ統合（venv / Node 依存 / ビルド / 設定 / linger /
  管理者の不足を自動判定して整えてから起動。旧 scripts/* は互換ラッパー化）
- **修正**: ログインレート制限を「失敗のみカウント」方式へ（正規ユーザーの連続ログインで誤制限しない）

検証: pytest 31 件成功（ファイル API roundtrip / トラバーサル / symlink 脱出 / viewer 権限 /
ターミナルライフサイクル / ブルートフォース制限）。実機 E2E（アップロード→ダウンロード、
symlink→/etc が 403、WS ターミナルで echo 実行→再接続リプレイ）。Playwright で 1280/390/320px
確認（横スクロール 0、コンソールエラーなし、モバイル全画面ターミナル + キーバー表示）。

## 実装済み機能

### バックエンド（FastAPI + SQLite WAL）
- 認証: Argon2id / サーバー側セッション（HttpOnly + SameSite Cookie、DB はトークンハッシュのみ）/
  CSRF（X-Requested-With 必須）/ ログインレート制限 / セッション一覧・失効
- RBAC: administrator / operator / viewer。REST・WebSocket 双方で権限依存性を強制
- 監査ログ: ログイン成功・失敗 / アプリ登録・編集・削除・起動・停止・強制終了 / ログ削除 / 電源操作
- アプリ管理: python_script / shell_script / executable / systemd_service（ユーザーユニット）
  - systemd ユーザーユニット生成（`cdapp-{id}.service`、引数エスケープ + インジェクション対策 + StartLimit 再起動ループ検出）
  - start / stop / restart / kill、8 状態マッピング、PID / 稼働時間 / CPU / RAM 取得
  - 環境変数は Fernet 暗号化保存、表示時は秘密キーをマスク
  - Python インタープリター自動検出 / プロジェクト検出（提示のみ）
- ログ: stdout / stderr の append 保存、tail / ダウンロード / 削除 / WS ストリーム
- 監視: psutil + GPU プロバイダー（amd-smi → rocm-smi → sysfs → nvidia-smi、失敗時 N/A）
  - 単一メトリクス WS ストリーム、1 分平均を SQLite へ保存（保持期間つき）、RAPL による CPU 電力推定
- 電源: reboot / shutdown / 予約（メモリ内、Web 再起動で消える制約は既知）

### フロントエンド（React + TS + Vite + Tailwind v4、gzip 約 99KB）
- ログイン / 認証ガード / 401 自動リダイレクト
- デスクトップ: 折りたたみサイドバー、Ctrl+K コマンドパレット（アプリ検索・起動停止・ページ移動・電源）
- モバイル: 下部ナビ 5 項目（中央「操作」→ボトムシート、電源は視覚分離）、Safe Area 対応、FAB
- ダッシュボード: CPU / RAM / GPU / VRAM タイル + スパークライン + 実行中 / 失敗アプリ
- アプリ: カード（主操作 1 個 + ⋯メニュー）、詳細ボトムシート、3 ステップ追加フロー（venv・エントリーポイント自動提案）、削除確認ダイアログ
- ログ: WS リアルタイム追従、仮想スクロール（2 万行保持）、正規表現検索、一時停止、stdout/stderr 切替、DL / 削除
- システム: ホスト / CPU コア別バー / GPU / ディスク / ネットワーク / 上位プロセス
- 設定: テーマ（システム / ライト / ダーク）、セッション管理、監査ログ閲覧（admin）
- 楽観的 UI（起動→即 STARTING）、WS 自動再接続 + 再接続中バッジ、タブ非表示時は購読停止

### スクリプト / 運用
- `scripts/setup.sh`（venv + npm + build + linger）、`run-dev.sh`（起動時 venv 自動構築）、
  `create-admin.sh`、`install-service.sh`（systemd ユーザーサービス、root 不要）

## 検証結果（2026-07-12、Ubuntu 24.04 実機）

- pytest 19 件成功（認証 / CSRF / 権限 / ユニット生成エスケープ / パストラバーサル / symlink 脱出 / マスキング）
- API E2E: 登録→起動→ログ→再起動→停止→ログ削除→監査を curl で確認
- **プロセス継続性**: Web バックエンド kill 後もアプリ継続、再起動後に同一 PID・稼働時間を取得
- WS: 未認証 403 / 偽 Origin 403 / 認証済みメトリクス・ログストリーム受信 OK
- GPU: AMD GPU を amd-smi で取得（使用率 / VRAM 23.3/32GB / 温度 / Hotspot / 電力 / ファン）
- UI: Playwright で 1280 / 390 / 320px を検証。横スクロール 0px、コンソールエラーなし。
  ダッシュボード / アプリ / ログ / システム / 設定 / 操作シート / 追加ドロワー / パレットのスクリーンショット確認
- systemd サービス: `control-deck-web` をユーザーサービスとして登録、非 root（一般ユーザー）で稼働、
  linger 有効化により SSH / ログアウト後も継続

## 既知の制約 / 次の作業

1. 電源の予約実行はプロセス内タイマー（Web 再起動で消失）→ helper + systemd timer へ移行予定
2. system レベルの systemd サービス制御は未対応（ユーザーユニットのみ）→ helper (polkit) で対応予定
3. アラート、アプリごとの GPU 使用量、アイコンアップロード、ヘルスチェックは未実装
4. Phase 4 以降（ファイル / ターミナル / ワークフロー / リモート / TOTP）は未着手
5. 電源 reboot/shutdown は API 実装済みだが実機での破壊的実行は未検証

## 履歴

- 2026-07-12: リポジトリ初期化。要求仕様原本と初期文書 8 点を記録
- 2026-07-12: PR #1 バックエンド（認証 / RBAC / 監査 / アプリ管理 / systemd / 監視 / 電源 / スクリプト）
- 2026-07-12: PR #2 フロントエンド（レイアウト / ダッシュボード / アプリ / ログ / システム / 設定）+ amd-smi パーサー修正
- 2026-07-13: リモートデスクトップ描画の根本修正（WS トンネルの Guacamole 命令境界保存）+ タッチ操作をタッチパッド方式に刷新（長押しドラッグ / 2本指右クリック / 3本指キーボード）+ タッチ端末は2倍解像度で接続し縮小表示
- 2026-07-13: ターミナル永続化の根本修正（tmux を systemd-run --user --scope で独立 cgroup 起動。サービス再起動で tmux ごと kill されていた）+ WS 自動再接続 + モバイル向けコピー/貼り付けシート
- 2026-07-13: Web スクレイピング強化: 抽出ビューワ（サニタイズ HTML をサンドボックス iframe に描画→要素クリックで CSS セレクタ自動生成、候補セレクタ一覧、抽出ワード↔結果の対比プレビュー）、複数抽出項目（各項目が出力変数、属性 text/html/href/src 等・複数取得選択可）、単一 selector との後方互換 + 下部ナビを fixed からフロー内配置に変更（iOS Safari 下部ツールバーによる浮き上がりバグ修正）
- 2026-07-13: ワークフロー v3（Dify/n8n 流）: トリガーに型付き入力フィールド定義（テキスト/長文/数値/選択/ファイル、実行時入力ダイアログ）、全後段から参照できる変数ピッカー（ノード出力メタデータ + 名前付き変数 {{vars.*}}）、LLM の稼働サーバー検出 + 構造化出力（json_object / json_schema + プリセット、非対応サーバーへはプロンプト埋め込みフォールバック）、全ノードに出力変数名設定、新ノード util.now / http.download
- 2026-07-13: GitHub 管理（リポジトリ登録でクローン/更新/保存/リバート/削除をボタン操作、~/ControlDeckApps へ格納、gh auth login のターミナル連携）+ 下部ナビ再編 + Overlay フォーカス奪取バグ修正
- 2026-07-13: アプリに Web ボタン（プロセスツリーの LISTEN ポートを検出しブラウザで開く。複数ポートは初回選択→ web_port として保存、設定編集で検出ポートから変更可）
- 2026-07-13: アプリ機能の使い勝手改善: テスト実行のストリーミング化（WS /apps/test-run/stream、常駐アプリ対応・停止ボタン）、実行 cwd を既定ホームに（test-run とユニットの WorkingDirectory）、パス入力にサーバー側ファイル選択ダイアログ（FilePicker）、リモートビューアのタッチ判定を pointer:coarse に精緻化
