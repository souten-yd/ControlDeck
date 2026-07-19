# Ubuntu Web Control Center — 要求仕様書（原本記録）

記録日: 2026-07-12
本書はユーザーから提示された「詳細設計・UI/UX設計・Claude Code実装指示書」の原本記録である。
実装はこの仕様に基づき Phase 単位で進める（進捗は docs/implementation-status.md を参照）。

補足要求（2026-07-12 追記）:
- Python 環境は venv 等を利用し、起動時に自動構築すること
- それ以外に必要なもの（Node 依存、システムパッケージ等）はセットアップスクリプトを用意すること
- 自己メンテナンス機能（ウォッチドッグ監視等）を備えること
- ノーコードワークフローで以下を行えること: LLM の操作 / ループ処理 / WEB スクレイピング・スクラップ /
  OCR / RAG 構築 / データベース操作 / WOL / ファイル操作 / コード実行 / 変数操作 / 文字操作 /
  markdown 処理 / ファイル出力 / Python コード実行
- ワークフローエディターはモダンでクールなグラフィックとすること

---

# 1. プロジェクト概要

Ubuntu上で動作する、PC、Pythonプログラム、シェルスクリプト、一般アプリケーション、ファイル、ターミナル、ワークフロー、リモートデスクトップをWebブラウザから一元管理できるセルフホスト型管理アプリケーションを開発する。

本アプリケーションは、単なるプロセス起動ツールではなく、Ubuntu PCを遠隔管理するための統合コントロールセンターとする。

仮称: `Ubuntu Control Deck`

名称、ロゴ、テーマカラーは設定から変更可能とする。

# 2. 目的

以下の操作を、PCおよびiPhoneのWebブラウザから安全かつ快適に実行できることを目的とする。

- Pythonプログラムの登録、起動、停止、再起動
- Python実行ファイル、仮想環境、スクリプト、作業ディレクトリの指定
- シェルスクリプト、実行ファイル、systemdサービスの管理
- SSH切断後も継続するプロセス管理
- CPU、GPU、VRAM、RAM、ディスク、温度、消費電力、ネットワークの監視
- アプリごとのCPU、RAM、GPU、VRAM使用量の確認
- 標準出力、標準エラー、アプリログの保存
- ログのリアルタイム表示、検索、ダウンロード、削除
- PCの再起動、シャットダウン、予約実行
- Webターミナル
- Webファイルマネージャー
- ファイルのアップロード、ダウンロード、編集、コピー、移動
- Webリモートデスクトップ
- Dify、n8nライクなノーコードワークフロー
- TOTP Authenticatorによる二要素認証
- ユーザー、ロール、権限管理
- 操作履歴、監査ログ
- PC、タブレット、iPhoneに対応したレスポンシブUI

# 3. 最重要設計原則

## 3.1 セキュリティ

1. Webサーバーをroot権限で動作させない
2. 通常操作と特権操作を分離する
3. 再起動、シャットダウン、systemd操作は専用Helper経由で実行する
4. 任意コマンド実行は初期状態で無効にする
5. すべての重要操作を監査ログへ記録する
6. ファイルアクセス可能範囲を設定で限定する
7. パストラバーサルとシンボリックリンク脱出を防止する
8. 外部公開よりLAN、VPN、Tailscale経由を推奨する
9. HTTPSを利用する
10. TOTP二要素認証を有効化できるようにする
11. WebSocketにも認証と権限確認を適用する
12. 秘密情報をログへ記録しない
13. アプリやワークフローの実行引数を安全に処理する
14. `shell=True`を原則禁止する

## 3.2 プロセス継続性

Webから起動したプログラムは、以下が発生しても停止しないこと。

- ブラウザを閉じた / iPhoneがスリープした / WebSocketが切断された
- SSH接続が終了した / 起動元端末を閉じた
- Webフロントエンドを再読み込みした / Webバックエンドが一時再起動した

アプリケーションの実行は、systemdまたはsystemd transient unitを利用する。
Webプロセスの子プロセスとして直接ぶら下げるだけの実装は禁止する。

## 3.3 UI・UX

- 洗練されたモダンデザイン / ごちゃごちゃしていない / 高速かつ軽量
- 情報量は多いが、常時表示は必要最小限
- PCとiPhoneの両方で快適に操作できる
- すべての機能へ2ステップ以内で到達できる
- 全ボタンを常時表示しない
- フローティングメニュー、ボトムシート、コンテキストメニューを活用する
- 破壊的操作だけは誤操作防止を優先する
- 管理画面特有のゴテゴテ感を避ける
- ページ遷移を最小化する / 必要に応じて詳細情報を段階的に表示する
- スマートフォンでも片手操作しやすい

# 4. UI・UX基本コンセプト

## 4.1 コンセプト

`Minimal / Fast / Contextual / Progressive Disclosure / Touch Friendly / Keyboard Friendly`

アプリカードの常時表示: アイコン / アプリ名 / 状態 / 稼働時間 / CPUまたはRAMの簡易値 / 主操作ボタン1個 / その他メニュー。
起動中なら主操作を「停止」、停止中なら「起動」とする。
再起動、ログ、設定、複製、削除などはメニューまたは長押しメニューに収納する。

## 4.2 2ステップアクセス原則

どの主要機能にも2ステップ以内でアクセス可能にする。主要機能を3階層以上の深いメニューへ格納しない。

## 4.3 段階的情報開示

初期表示では要点だけを表示し、詳細は展開パネル / ドロワー / ボトムシート / モーダル / フローティングパネル / 詳細ページ / 長押しメニュー / 右クリックメニューで開く。

## 4.4 高速UX（必須）

- 画面全体を毎回再読み込みしない / 楽観的UI / 起動操作後即座に`STARTING`表示
- スケルトンローディング / 不要なスピナー多用禁止 / キャッシュ状態の即時表示
- WebSocket切断時も最後の状態を保持しバックグラウンド再接続
- 大量ログ・大量ファイルは仮想スクロール
- チャート再描画は最小限 / 画面外コンポーネント停止
- iPhoneタブ切替後に過剰通信しない

## 4.5 デザイン方針

余白広め / 線・枠を多用しない / カード階層は最大2段階 / 影控えめ / 色は状態・重要度表現に限定 /
アイコン+短ラベル併用 / 主要ボタンは1画面1〜2個 / 赤は破壊的操作と重大エラーのみ /
等幅数字 / ダーク・ライト対応 / OLED完全黒テーマは任意設定。

# 5. 対応画面サイズ

- デスクトップ: 1280px以上。折りたたみ可能サイドバー+上部バー+メイン+右詳細ドロワー。
- タブレット: 768〜1279px。サイドバーはオーバーレイまたはアイコンのみ。2カラムまで。
- iPhone: 320〜767px。サイドバー非表示、下部ナビ、1カラム、ボトムシート、Safe Area対応
  （`env(safe-area-inset-bottom)` / `env(safe-area-inset-top)`）、横向き対応。
- PWA: 将来対応（ホーム画面追加、フルスクリーン、Push等）。Service Workerへ機密を保存しない。

# 6. ナビゲーション設計

- デスクトップサイドバー: 概要 / アプリ / ワークフロー / ファイル / ターミナル / リモート / システム / ログ / 設定。縮小時アイコンのみ。
- モバイル下部ナビ最大5項目: 概要 / アプリ / 操作 / ファイル / その他。中央「操作」はボトムシート
  （アプリ追加 / ターミナル / ワークフロー実行 / ファイルアップロード / PC再起動 / PCシャットダウン。電源操作は視覚的に分離）。
- コマンドパレット: Ctrl+K / Cmd+K。アプリ検索・起動・停止、ログ、ターミナル、ファイル検索、ワークフロー実行、システム監視、設定検索。
- グローバルフローティング操作: 画面依存FAB（アプリ追加 / 新規ワークフロー / アップロード / 新規セッション）。

# 7. 技術スタック

- バックエンド: Python 3.11+ / FastAPI / Uvicorn / SQLAlchemy 2.x / Alembic / Pydantic / psutil / asyncio / WebSocket / systemd / journald。
  バックグラウンドタスク第一候補 Redis + Dramatiq（代替 Celery / RQ）。MVPはRedisなしで起動可能。
- フロントエンド: React / TypeScript / Vite / React Router / TanStack Query / Zustand / Tailwind CSS /
  Radix UIまたはshadcn/ui / React Flow / xterm.js / Monaco Editor / EChartsまたはRecharts。軽量性優先。
- DB: 初期 SQLite、拡張 PostgreSQL（切替可能な構成）。
- リモートデスクトップ: 第一候補 Apache Guacamole（代替 GNOME Remote Desktop / TigerVNC / noVNC / websockify）。独自プロトコル実装禁止。

# 8. システム構成

```text
Web Browser → (HTTPS/WS) → React Frontend → FastAPI Backend
  ├── Authentication / Application Manager / Monitoring / Log Manager
  ├── Workflow Engine / File Manager / Terminal Gateway / RD Gateway
  ├── Notification / Audit
  └── Privileged Helper → systemd / reboot / shutdown / sensors / 許可済み特権操作
```

# 9. プロセス構成

- control-deck-web: 一般ユーザー権限。API / UI配信 / 認証 / DB / WebSocket / ファイル操作 / ログ表示 / データ集約。
- control-deck-agent: 状態監視 / メトリクス収集 / ログ収集 / ワークフロー実行 / ヘルスチェック / アラート判定。
- control-deck-helper: 最小特権。systemd操作 / 再起動 / シャットダウン / 特権センサー。
  通信: Unix Domain Socket / D-Bus / Polkit。Web APIから直接任意のsudo実行は禁止。

# 10. アプリケーション管理

## 10.1 登録種類

全体: Python Script / Python Module / Shell Script / Executable / systemd Service / Docker Container / Docker Compose / Custom Command / URL Shortcut。
MVP: Python Script / Shell Script / Executable / systemd Service。

## 10.2 Pythonアプリ登録項目

アプリ名 / 説明 / アイコン / カテゴリ / タグ / Python実行ファイル / スクリプト / Pythonモジュール / 作業ディレクトリ /
仮想環境 / 起動引数 / 環境変数 / 起動ユーザー / 自動起動 / 再起動ポリシー / 停止タイムアウト / 正常終了コード /
ヘルスチェック / 依存サービス / ログ設定。

## 10.3 Python自動検出

/usr/bin/python3, /usr/bin/python3.11, /usr/local/bin/python3, ~/.pyenv/, .venv/bin/python, venv/bin/python, Conda環境。
プロジェクト指定時: pyproject.toml / requirements.txt / Pipfile / poetry.lock / uv.lock / main.py / app.py / server.py / manage.py。
検出候補は提示のみとし、ユーザー確認なしで実行しない。

## 10.4 アプリ追加UI

デスクトップは右ドロワー、モバイルは全画面ボトムシートまたはステップ形式。最大3画面。
Step1: アプリ名 / アイコン / 種類 / プロジェクトフォルダ。
Step2: Python / スクリプト / 引数 / 作業ディレクトリ。
Step3: 自動起動 / 再起動ポリシー / ヘルスチェック / 確認。上級設定は折りたたみ。

## 10.5 アイコン

PNG / JPEG / WebP / SVG / URL / 組み込み / 頭文字。SVGサニタイズ、自動縮小。

## 10.6 起動方式

systemdを利用する（unit例は原指示書参照。StandardOutput=append:ログパス等）。
安全なテンプレート生成。`subprocess.run(user_input, shell=True)` 禁止。
配列引数 + cwd + safe_env + start_new_session を推奨するが、永続実行はsystemd優先。

## 10.7 状態

STOPPED / STARTING / RUNNING / STOPPING / RESTARTING / FAILED / DEGRADED / UNKNOWN。
状態色: RUNNING緑 / STOPPEDグレー / STARTING青 / FAILED赤 / DEGRADED黄 / UNKNOWN薄グレー。色+アイコン+文字併用。

## 10.8 アプリ一覧UI

デスクトップ: カード⇔コンパクトテーブル切替。カードは アイコン/名前/状態/稼働時間/CPU/RAM/主操作/メニュー。
モバイル: 1カラムカード。常時表示は アイコン/名前/状態/稼働時間/主操作。詳細はタップでボトムシート。
メニュー・長押し: ログ / 再起動 / 設定 / ターミナルで開く / 作業フォルダを開く / 自動起動 / 削除。

## 10.9 主操作

停止中→起動、実行中→停止、異常時→再起動。状態に応じて1個だけ表示。

## 10.10 再起動ポリシー

再起動しない / 異常終了時 / 常に / 成功終了時 / 最大回数指定 / 一定時間内失敗回数指定。再起動ループ検出。

## 10.11 ヘルスチェック

プロセス存在 / TCPポート / HTTP GET / HTTPステータス / レスポンス本文 / ファイル存在 / 許可済みコマンド。
プロセス存在するがHC失敗 → DEGRADED。

# 11. ログ管理

- 収集対象: stdout / stderr / systemd journal / 指定ログファイル / ワークフロー実行ログ / 監査ログ。
- ログUI: 上部は 検索 / 一時停止 / フィルター / その他メニュー（ダウンロード・表示範囲・折り返し・コピー・削除）。
  モバイルは検索と一時停止のみ表示。リアルタイム追従 / 自動スクロール / レベルフィルター / stdout・stderr切替 /
  時刻範囲 / キーワード / 正規表現 / 折り返し / 全文コピー / ダウンロード / 削除。仮想スクロール必須。
- 保持初期値: 30日 / 1アプリ最大1GB / 1ファイル100MB / 10世代 / gzip。
- マスキング: TOKEN / SECRET / PASSWORD / PASS / API_KEY / PRIVATE_KEY / AUTH / COOKIE。

# 12. システム監視

- ダッシュボード上部サマリー: CPU / RAM / GPU / VRAM / ディスク / 消費電力推定 / 稼働時間 / 実行中アプリ数。コンパクトに。
  その下: 主要グラフ / 実行中アプリ / 最近のアラート / 最近の操作。
- CPU: 全体・コア別使用率 / ロードアベレージ / クロック / 温度 / 上位プロセス。
- RAM: 総容量 / 使用量 / 使用率 / Available / Cache / Swap。
- GPU: AMD/NVIDIA/Intel自動判定。使用率 / VRAM / 温度 / Hotspot / クロック / ファン / 電力 / 電力上限 / GPUプロセス。
  AMD優先順位: amd-smi → rocm-smi → sysfs。NVIDIA: NVML → nvidia-smi。Intel: intel_gpu_top → sysfs。
  取得不可項目はN/A、全体エラー化禁止。
- ディスク: マウントポイント / 容量 / 使用率 / R/W速度 / IO待機 / SMART / 温度。
- ネットワーク: IF / IP / 受信送信速度 / 累積 / 接続状態 / リンク速度。
- システム: ホスト名 / Ubuntuバージョン / カーネル / 起動時刻 / 稼働時間 / 現在時刻 / タイムゾーン。
- 消費電力: CPU推定 / GPU / 合計推定 / 推定電力量。外部電力計なしの場合は推定値と明示。
- 更新頻度: 1/2/5/10/30/60秒、標準2秒または5秒。バックグラウンド時は低頻度化。
- 履歴: 生24時間 / 1分平均30日 / 1時間平均1年。期間: 15分〜任意。
- モバイル: 大量グラフ同時描画禁止。初期はCPU/RAM/GPUのみ。データ点間引き。

# 13. アラート

条件例: CPU90%が5分 / GPU温度90℃ / VRAM95% / RAM90% / ディスク残10%未満 / アプリ停止 / HC失敗 / 再起動ループ / ログERROR。
通知: Web / メール / Discord / Slack / Webhook。重大のみ上部表示、軽微は通知センター。

# 14. PC電源管理

再起動 / シャットダウン / 予約再起動 / 予約シャットダウン / 予約取消。将来: サスペンド / ハイバネート / WoL。
電源操作は常時露出させない。グローバル操作→電源→実行の2ステップ+確認ダイアログ。
確認画面: 実行中アプリ数 / 実行中ワークフロー / 接続中ターミナル / 接続中RD / 正常停止してから実行 / 即時実行。
TOTP再認証要求設定を用意。

# 15. Webターミナル

xterm.js / WebSocket / PTY / tmux。複数タブ / タブ名変更 / bash・zsh / 作業ディレクトリ / 再接続 / 検索 /
コピペ / 全画面 / 終了。Web切断でもtmux継続、再接続時attach。
モバイル: 全画面利用 / キーボード表示時に高さ再計算 / Ctrl・Alt・Tab・Esc・矢印の補助バー（横スクロール可）/
長押しコピペ / Safe Area / セッション切替はドロップダウンまたはボトムシート。
権限: 使用不可 / 一般ユーザー / 管理ターミナル。rootシェル直接提供禁止。

# 16. ファイル管理

- 基本: 一覧 / グリッド / アップロード / ダウンロード / 新規フォルダ・ファイル / 名前変更 / コピー / 移動 / 削除 /
  圧縮 / 解凍 / プレビュー / 編集 / ファイル情報。
- デスクトップ: 左ツリー / 中央一覧 / 右プレビュー（閉じられる）。
- モバイル: 1カラム / パンくず / タップでプレビュー / 長押し選択モード / 複数選択時のみ操作バー /
  アップロードFAB / 操作はボトムシート / ツリーはドロワー。
- プレビュー: テキスト / Markdown / JSON / YAML / 画像 / PDF / 音声 / 動画 / ログ。大容量はRange/ストリーミング。
- 編集: Monaco Editor（遅延ロード）。モバイルは軽量エディター可。
- 許可ルート例: /home/USER/projects, /data, /data1tb, /var/lib/control-deck/shared。
  初期拒否: /etc/shadow, /root, /proc, /sys, /dev, 秘密鍵, ブラウザプロファイル, 認証情報。
  realpath正規化+許可ルート配下確認。
- アップロード: D&D / 複数 / 進捗 / キャンセル / チャンク / 再開 / 上書き確認。iOS対応。
- ごみ箱: 復元 / 完全削除 / 保持期間 / 容量上限。

# 17. リモートデスクトップ

Guacamole第一候補。UI: 接続 / 全画面 / 解像度 / 画質 / クリップボード / 音声 / ファイル転送 / 切断。
接続後は表示領域最大化。モバイル: タップ=左クリック / 長押し=右クリック / 2本指スクロール / ピンチズーム /
仮想マウス / 仮想キーボード / 補助キー / 横向き全画面 / ツールバー自動非表示。
セキュリティ: 一時トークン / 接続先制限 / Origin検証 / タイムアウト / クリップボード・転送無効化設定 / 認証情報非露出。

# 18. ノーコードワークフロー

React Flowエディター。デスクトップ: 左ノード一覧 / 中央キャンバス / 右設定 / 下実行ログ。
モバイル: 全画面キャンバス / ボトムシート / 全画面設定シート / ピンチズーム / 長押し追加。
トリガー: 手動 / 日時 / Cron / 間隔 / PC起動時 / シャットダウン前 / アプリ開始・停止 / ファイル作成・変更 /
CPU・GPU・ログ条件 / Webhook。
ノード: アプリ操作（起動/停止/再起動/状態/ログ/HC）、システム操作（再起動/シャットダウン/待機/情報/通知、危険ノードは管理者のみ）、
コマンド（登録済みのみ、任意シェルは初期無効）、ファイル、ネットワーク（HTTP GET/POST/Webhook/TCP/Ping）、
条件・制御（If/Switch/AND/OR/NOT/待機/再試行/タイムアウト/並列/順次/エラーハンドラー/終了）。
実行状態: QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELED / TIMED_OUT / WAITING。ノードごとの入出力・時刻・エラー保存。
- 実行履歴はノードごとの実入力、実出力、開始・終了・経過時間、retry、token、log、error、artifact参照、入出力sizeを表示し、並列性とbottleneckをタイムラインで確認できる。
- エディタの確認操作は「実行前チェック」と「下書きをテスト」を区別する。両方で同じ構造検証、副作用、公開可否を表示し、前者はexecutor・Secret復号・外部通信・書込を行わず、後者だけがdraftを実実行する。
- エディタの主操作は「更新して開く」とし、未保存変更を保存してblockingがなければ現在draftを差分時だけ公開し、公開アプリ画面へ移る。変更がない場合は「アプリを開く」と表示しversionを増やさない。
- 公開アプリは入力・進捗・承認・型付き出力・履歴だけを扱い、編集定義を返さない専用APIを使う。承認待ちはnode ID、redact済み承認文、担当者、ISO 8601期限を共通contractで返し、公開アプリ内の承認／却下で再開できる。workflow IDをURLへ保持し、再読込後も選択を復元する。エディタ内の直接実行は高度なデバッグ操作としてその他メニューへ置く。
- schedule、Webhook、system event、外部API、公開アプリはdraftを暗黙公開せず、明示済み公開版だけを実行する。ワークフロー一覧から既存公開版を開いても、編集中draftを更新しない。
- LLM生成ノードは、ControlDeck管理下のローカルruntimeが停止・unload中なら生成前に正規のモデル管理境界から起動・ロードし、有限のstartup timeout内でhealth完了を待つ。同一modelの並列起動をまとめ、外部endpointは勝手に操作しない。自動準備は既定有効とし、進捗と起動／load／timeoutの失敗理由をNodeRunへ残す。

# 19. 認証

- ログイン: ユーザー名 / パスワード / TOTP / リカバリーコード。
- TOTP: 標準TOTP互換。有効化→QR→6桁確認→リカバリーコード→完了。設定: 任意 / 管理者必須 / 全ユーザー必須。
- パスワード: Argon2id。
- セッション: Secure / HttpOnly / SameSite / サーバー側セッション。localStorage長期JWT禁止。
- ロール: Administrator / Operator / Viewer / Custom。
- 権限: アプリ閲覧・起動・停止・編集・削除 / ログ閲覧・削除 / ファイル閲覧・編集・削除 / ターミナル /
  ワークフロー編集・実行 / システム監視 / 電源操作 / リモートデスクトップ / ユーザー管理 / 設定。

# 20. セキュリティ詳細

- ネットワーク: 初期127.0.0.1。LAN公開時0.0.0.0+警告。推奨: Tailscale / WireGuard / LAN / Reverse Proxy+HTTPS。
- HTTPS: Caddy / Nginx / Traefik。
- CSRF: Cookie認証時は対策必須。
- WebSocket: ログイン / Origin / 権限 / 対象リソース / 有効期限確認。
- レート制限: ログイン / TOTP / パスワード変更 / API / WebSocket / ダウンロード。
- 任意コマンド: 安全モード（テンプレートのみ）/ 高度モード（初期無効、管理者+TOTP再認証で有効化可能）。
- 環境変数: ホスト環境を無制限に引き継がない。警告対象: LD_PRELOAD / PYTHONPATH / BASH_ENV / ENV / PROMPT_COMMAND。
- 監査ログ記録対象: ログイン成功・失敗 / ログアウト / TOTP変更 / アプリ登録・編集・起動・停止 / 強制終了 /
  ログ削除 / ファイル操作 / ターミナル開始 / ワークフロー編集・実行 / 再起動 / シャットダウン / ユーザー変更 /
  権限変更 / 設定変更。

# 21. データモデル

User / Session / ManagedApplication / ApplicationInstance / HealthCheck / LogSource / Workflow / WorkflowExecution / AuditLog
（各フィールドは原指示書のとおり。User: id, username, display_name, password_hash, role_id, is_active, totp_enabled,
totp_secret_encrypted, created_at, updated_at, last_login_at。ManagedApplication: id, name, description, application_type,
icon_path, working_directory, executable_path, script_path, python_path, arguments_json, environment_json_encrypted,
run_as_user, auto_start, restart_policy, stop_timeout_seconds, health_check_id, status, systemd_unit_name, created_at,
updated_at。他モデルも同様）

# 22. API

ベース `/api/v1/`。

- 認証: POST /auth/login, /auth/logout, /auth/totp/setup, /auth/totp/verify, /auth/totp/disable, GET /auth/sessions, DELETE /auth/sessions/{id}
- アプリ: GET/POST /apps, GET/PATCH/DELETE /apps/{id}, POST /apps/{id}/start|stop|restart|kill|health-check
- ログ: GET /apps/{id}/logs, GET /apps/{id}/logs/download, DELETE /apps/{id}/logs, WS /apps/{id}/logs/stream
- システム: GET /system/overview|cpu|memory|gpu|disk|network|processes|metrics/history, WS /system/metrics/stream
- 電源: POST /system/reboot, /system/shutdown, /system/power/schedule, DELETE /system/power/schedule
- ファイル: GET /files/list|info|download|preview|text, POST /files/upload|directory|copy|move, PATCH /files/rename, DELETE /files, PUT /files/text
- ターミナル: POST/GET /terminals, DELETE /terminals/{id}, WS /terminals/{id}/connect
- ワークフロー: GET/POST /workflows, GET/PATCH/DELETE /workflows/{id}, POST /workflows/{id}/run|enable|disable,
  GET /workflow-executions, GET /workflow-executions/{id}, POST /workflow-executions/{id}/cancel

# 23. フロントエンド性能要件

- 初期JS最小化、主要画面2秒以内操作可能。
- 遅延ロード: Monaco / React Flow / xterm.js / リモートデスクトップ / 高度グラフ / 管理者設定。
- 再レンダリング: メトリクス更新で全体再描画禁止 / Zustandセレクター / React.memo / 仮想化 /
  WebSocketの過剰state反映禁止 / 1秒更新でもUI阻害禁止。
- 通信: TanStack Queryキャッシュ / WS用途統合 / ログストリームは開いているアプリのみ / 非表示画面の購読停止。
- アニメーション: 100〜200ms / 大移動禁止 / prefers-reduced-motion対応 / 数値の激しい揺れ禁止。

# 24. アクセシビリティ

キーボード操作 / フォーカス表示 / ARIA / 色以外の状態表現 / コントラスト / タッチ44px以上 /
iPhone文字サイズ変更耐性 / VoiceOver / フォーカストラップ / Escで閉じる / 破壊的操作の明確な文言。

# 25. UI状態管理

Idle / Loading / Success / Warning / Error / Offline / Reconnecting / Permission Denied を明確に表示。
起動: STOPPED→STARTING→RUNNING、失敗: STARTING→FAILED。
エラー時は 簡潔な原因 / 推奨される次の操作 / 詳細ログを開く / 再試行 を表示。スタックトレース直接表示禁止。

# 26. インストール

install.sh / Debian package 推奨。Docker版は補助的対応。
インストール時: 専用ユーザー / Python仮想環境 / フロントエンドビルド / ディレクトリ作成 / 権限設定 /
systemd登録 / 初期管理者 / サービス起動。
systemd: control-deck-web.service / control-deck-agent.service / control-deck-helper.service
（必要に応じ worker / scheduler）。

# 27. 設定例

```yaml
server:
  host: 127.0.0.1
  port: 8765
  public_url: https://control.example.local
  trusted_proxies: []
security:
  require_totp_for_admin: true
  allow_arbitrary_commands: false
  session_timeout_minutes: 480
  reauthentication_minutes: 15
ui:
  default_theme: system
  compact_desktop: false
  mobile_bottom_navigation: true
  floating_actions: true
  command_palette: true
  animations: reduced
  metric_refresh_seconds: 2
files:
  allowed_roots:
    - /home/USER/projects
    - /data
    - /data1tb
  trash_enabled: true
  max_upload_size_gb: 100
logs:
  retention_days: 30
  max_total_size_gb: 10
  rotate_size_mb: 100
monitoring:
  interval_seconds: 2
  history_enabled: true
  raw_retention_hours: 24
  minute_retention_days: 30
remote_desktop:
  enabled: false
  provider: guacamole
  base_url: http://127.0.0.1:8081
terminal:
  enabled: true
  persistent_sessions: true
  backend: tmux
```

# 28. ディレクトリ構成

backend/（app/api, auth, applications, monitoring, logs, workflows, files, terminals, remote_desktop, audit,
notifications, database, models, schemas, security, main.py + tests + alembic + pyproject.toml）、
frontend/（src/api, components, layouts, pages, features, hooks, stores, styles, types + package.json）、
helper/（control_deck_helper, polkit, systemd）、scripts/（install.sh, uninstall.sh, backup.sh, restore.sh）、
deploy/（systemd, caddy, nginx）、docs/、AGENTS.md、README.md、LICENSE。

# 29. 実装フェーズ

- Phase 1: FastAPI / React / SQLite / ログイン / 管理者 / RBAC / 基本ダッシュボード / レスポンシブ / モバイル下部ナビ
- Phase 2: Pythonアプリ登録 / 起動 / 停止 / 再起動 / systemd / 状態表示 / アイコン / ログ
- Phase 3: CPU / RAM / GPU / VRAM / ディスク / ネットワーク / 消費電力 / 履歴 / アラート
- Phase 4: ファイルマネージャー / アップロード / ダウンロード / 編集 / Webターミナル / tmux
- Phase 5: React Flow / トリガー / 条件 / アプリ操作 / ファイル操作 / HTTP / 実行履歴
- Phase 6: Guacamole / モバイルリモート操作 / 接続管理
- Phase 7: TOTP / 通知 / バックアップ / PostgreSQL / PWA / Wake-on-LAN / プラグイン

# 29.1 ワークフロー設定支援

- 新規ノードは、実行環境に依存せず安全に決定できる設定を初期値として持つ。URL、path、model、Secretなどは推測して埋めない。
- バックエンドnode metadataを正として、各config fieldの必須性、型、executor既定値、推奨値、推奨理由を返す。
- ユーザーが入力済みの値を上書きせず、空欄だけへ推奨設定を一括適用できる。
- ノード接続時、対象ノードの主要入力が空なら、直前ノードの代表出力を参照式として補完する。
- 入力エディタから、直前ノード、その他の上流ノード、名前付き変数を検索し、型と直近実行サンプルを確認してカーソル位置へ挿入できる。
- ノード設定内から、用途、必須設定、主な出力、副作用、最短手順、構成例、各設定値の理由を参照できる。
- 推奨値と構成例の適用は1操作で行えるが、既存値とSecretを暗黙に変更しない。

# 29.2 Project Lab

- `~/CodeDEV`直下のprojectを設定なしで検出し、Python、Node/Web、CMake、Rust、.NET、静的HTML、Git状態を表示する。
- 検出だけではprogramやscriptを起動しない。CLI/test/Webはユーザーの明示操作でのみ、browserから独立した制限付きsystemd user unitとして実行する。
- `.controldeck/project.json`はversioned schemaで検証し、command文字列を禁止してargv配列、project内cwd、非秘密environment、Secret名参照を使用する。
- HTML、画像、CSV/TSV、JSON、Markdown、PDF、audio/video、log/textを型別表示し、巨大text/表にはpreview上限を設ける。
- CodeDEV外path、symlink escape、秘密file名、`.env`、source code、依存cacheを成果物として公開しない。
- HTMLはCSPとsandbox iframe、artifactは認証・権限・MIME検査・path containmentを必須とする。
- JSON、表、log/textのinline previewでは秘密らしいkey/valueを伏せ字化する。
- 実行履歴には終了状態、終了code、時間、redact済みlog、生成・変更artifact metadataを保存し、Secret値と巨大artifact本文をDBへ保存しない。
- Secret参照profileは安全なcredential注入が利用できるまで実行拒否し、未対応を成功扱いにしない。
- Web previewはrunへ自動割当したlocalhost portだけを対象とし、unitのprocess treeによるLISTENを確認する。ControlDeckのCookie/Authorization/CSRFと上流Set-Cookieを転送しない。

# 30. MVP完了条件

1. Ubuntu起動時に自動起動する
2. PCとiPhoneからログインできる
3. Python実行ファイルを指定できる
4. Pythonスクリプトを指定できる
5. 作業ディレクトリを指定できる
6. アプリを起動できる
7. アプリを停止できる
8. アプリを再起動できる
9. SSH切断後も継続する
10. Web再起動後も状態を取得できる
11. stdout、stderrを保存できる
12. ログを表示できる
13. ログをダウンロードできる
14. ログを削除できる
15. CPU、RAM、GPU、VRAMを表示できる
16. PC稼働時間を表示できる
17. 再起動、シャットダウンができる
18. ファイルを送受信できる
19. Webターミナルを利用できる
20. 重要操作が監査ログへ残る
21. TOTPを有効化できる
22. 任意コマンドは初期無効
23. Webサービスがrootで動作していない
24. 許可ルート外へアクセスできない
25. PCレイアウトが破綻しない
26. iPhone縦画面で主要操作が可能
27. iPhone横画面でターミナルとリモート表示が可能
28. 主要機能へ2ステップ以内で到達できる
29. 各一覧で全操作ボタンを常時並べていない
30. 大量ログ表示中も操作が重くならない

# 31. UI受け入れ条件

- デスクトップ: サイドバー縮小可 / 不要ボタンなし / 起動停止1操作 / その他は+1操作 / Ctrl+K / ダッシュボード一画面。
- iPhone: 下部ナビSafe Area / タップ領域十分 / 横スクロール前提禁止 / 片手操作 / ボトムシートはみ出し禁止 /
  キーボード表示時ターミナル破綻禁止 / iOSファイル選択対応 / RD横向き全画面 / 2ステップ以内 / 破壊的操作常時露出禁止。
- 性能: 全体リロード禁止 / メトリクス更新中も滑らか / ログ1万行操作可 / 仮想スクロール / 不要チャート同時描画禁止 /
  Monaco・React Flow・xterm.js遅延ロード。

# 32. テスト

- 単体: 認証 / 権限 / パス検証 / systemdユニット生成 / ログローテーション / ワークフロー / 暗号化。
- 統合: 登録〜起動〜ログ〜停止〜再起動 / 異常終了 / 自動再起動 / Web再起動 / TOTP / ファイル / ターミナル / ワークフロー。
- セキュリティ: パストラバーサル / シンボリックリンク脱出 / コマンドインジェクション / CSRF / XSS / WS未認証 /
  権限昇格 / ブルートフォース / 悪意あるSVG / ZIP Slip / 巨大アップロード。
- UI: Chrome/Firefox Desktop, Safari iPhone, Chrome Android相当。1280/1920/390/375/320px、横向き。
- 障害: systemd停止 / GPU監視不可 / DBロック / ディスク満杯 / WS切断 / 再接続 / 再起動ループ / バックエンド再起動。

# 33. Claude Codeへの実装指示

Phase単位で進める。各Phaseで: リポジトリ調査 → 実装計画 → 責務明確化 → PCレイアウト実装 → iPhoneレイアウト実装 →
単体テスト → Ubuntu起動 → API確認 → PCブラウザ確認 → モバイル幅確認 → systemd継続確認 → SSH切断継続確認 →
エラー修正 → 文書化。

実装制約（抜粋）: rootで起動しない / shell=True原則禁止 / シェル文字列連結禁止 / パス正規化必須 / symlink脱出防止 /
秘密値ログ出力禁止 / API・WSごとに権限確認 / 破壊的操作の監査 / GPU監視失敗で全体停止禁止 / Ubuntu 24.04系 /
Python 3.11正式対応 / amd-smi・rocm-smi優先 / NVIDIA追加可能構造 / PC・iPhone正式対応 / ダーク・ライト対応 /
2ステップ以内 / 全ボタン常時表示禁止 / ボトムシート活用 / ドロワー・コマンドパレット活用 / 遅延ロード / 仮想化 /
エラー握り潰し禁止 / ユーザー向けエラーと内部ログ分離 / 装飾過多禁止 / 主要CTA1〜2個 / トーストで完了通知 /
破壊的操作のみ確認ダイアログ。

最初に作成する文書: README.md / AGENTS.md / docs/architecture.md / docs/security-model.md /
docs/ui-ux-guidelines.md / docs/mobile-layout.md / docs/implementation-plan.md / docs/implementation-status.md。

最初に実装する機能: ログイン / 管理者作成 / レスポンシブレイアウト / デスクトップサイドバー / モバイル下部ナビ /
グローバル操作メニュー / アプリ登録 / Pythonパス指定 / スクリプト指定 / 作業ディレクトリ指定 / 起動 / 停止 / 再起動 /
systemd継続実行 / 状態取得 / stdout / stderr / CPU / RAM / GPU / VRAM / 監査ログ。

UI実装時の自己レビュー項目: 常時表示ボタンの必要性 / メニュー移動可能性 / 主要操作の明確さ / 2ステップ到達 /
片手操作 / 320px / 長文テキスト / エラー復旧 / 切断状態表示 / レイアウトの跳ね。

コードを書くだけで完了とせず、Ubuntu上で実際に起動し、PCブラウザとモバイル幅の両方で操作確認し、
問題を修正してから完了扱いとする。
