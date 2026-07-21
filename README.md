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
- React Flow ベースのビジュアルエディタ。標準 45 種類 + OpenCode 有効時の `code.agent` ノード:
  アプリ制御 / 条件分岐 / ループ / 変数 / 文字列操作 / ファイル入出力 / HTTP / ダウンロード /
  スクレイピング / ブラウザ操作（Playwright）/ LLM / RAG / Deep Research / OCR / DB クエリ / SSH / Git / C++ ビルド / WOL / Webhook 通知 / 現在日時 など
- **トリガー入力フィールド**: テキスト / 長文 / 数値 / 選択 / ファイルの型付き入力を定義し、実行時ダイアログで受け取る
- **変数ピッカー**: 上流ノード（直前に限らず全経路）の出力を一覧から選んで `{{ノードID.フィールド}}` を挿入。「出力変数名」を付ければ `{{vars.名前}}` でも参照可能
- **LLM ノード**: 稼働中の OpenAI 互換サーバー（Ollama / llama.cpp / LM Studio / vLLM 等）を自動検出してワンタップ設定。構造化出力（JSON スキーマ + プリセット、非対応サーバーへは自動フォールバック）
- **実行前チェック**: executor を呼ばず、実行順・分岐・予定されるファイル書込や外部通信・必要 capability・公開可否を事前確認
- **確認・テスト**: 入力、予定副作用、実行前チェック、draftテスト、公開可否、型付き最終出力、ノード結果、過去入力の再利用を同じ画面で確認
- **ノード実行観測**: 実行履歴からGantt風タイムライン、ボトルネック、実入力・実出力・時間・retry・token・ログ・エラー・artifact参照をノード単位で検査
- **AI 生成の検証**: 生成したフローを構造・意味の両面で検証し、品質スコアと修正点を表示してから登録
- **ノードカタログ**: 検索、カテゴリ、localStorage お気に入り、利用可能ノード絞り込み、未導入機能の案内。実行中は対応ノードの進捗を表示
- JSON / CSV 変換、Schema 検証、許可ルート内 glob、embedding / rerank / LLM judge、分離 context による並列 map に対応
- `human.approval`で承認者・期限付きの人手確認、`control.merge`でwait-all／first-success／first-complete／quorum／collect合流に対応
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

## 最近の主な追加（2026-07-15〜19）

- OpenCode を既定無効のオプトイン feature として統合
- 独立 AI アシスタント、永続会話、ジョブの優先度・進捗 stream・cancel を追加
- llama.cpp の複数 GGUF 管理と Ollama / 外部 OpenAI 互換 provider の共通モデル操作を追加
- ワークフローを標準45ノードへ拡張し、生成時の意味検証・品質スコア、実行前チェック、並列map、型・side effect metadata、検索・お気に入りを追加
- 実行snapshot、node run、単体／部分再実行、固定データ、回帰テスト、draft／公開版分離、型付き`output.render`を追加
- AIアシスタントと`research.deep`を反復型Deep Research共有エンジンへ統合し、SearXNG、PDF、学術、GitHub、RAG、ローカルコード、特許、市場資料に対応
- LLMを使わない確定的な`data.template`、arrayのfilter/sort/unique/limitを行う`data.filter`、group集計対応の`data.aggregate`を追加
- 承認文・承認者・期限を持つ`human.approval`と、5方式で分岐を合流する`control.merge`を追加
- アプリアイコン、TCP / HTTP / ファイルのヘルスチェック、ごみ箱、再開可能アップロード、永続電源予約を追加
- AMD GPU 監視を sysfs fast path へ移行し、Web ポーリングとジョブ通知を軽量化
- AI アシスタントと Web ターミナルの 320px / iOS 向け入力・再接続・履歴復元を改善
- ワークフロー生成を標準 JSON Schema payloadへ修正し、固定800 tokenではなくModel画面の共通出力上限を使用

詳細と検証結果は [実装状況](docs/implementation-status.md) を参照。

## 機能別ガイド

### ダッシュボードとシステム監視

ログイン直後のダッシュボードでは、CPU、RAM、GPU、VRAM、温度、電力、ディスク、ネットワークと、実行中／異常アプリをまとめて確認できる。
詳細なコア別使用率、GPUセンサー、ディスク、ネットワーク、上位プロセスは「システム」で見る。センサー取得に失敗した項目は`N/A`となり、監視全体は停止しない。

継続的な監視が必要な場合は、設定からしきい値、継続時間、再通知までのクールダウン、Discord／Slack／汎用Webhook通知先を登録する。
一時的なスパイクを継続時間で除外でき、同じ異常を短時間に連投しない。GPUがないPCでもCPU／RAM／ディスク監視は利用できる。

### アプリ登録、起動、ログ、ヘルスチェック

「アプリを追加」でPython、shell script、実行ファイル、既存systemd service、URL shortcutを選び、実行パス、作業ディレクトリ、引数、環境変数を設定する。
パスはサーバー側ファイル選択から選べる。Pythonプロジェクトはvenvとentry point候補を検出し、登録前のストリーミング動作確認でstdout/stderrを確認できる。

登録後の主操作は起動／停止の1つに絞り、再起動、強制終了、編集、ログ、削除はその他メニューへまとめている。実プロセスはsystemd user unitで動き、WebサービスやSSHを閉じても継続する。

既存のsystem scope serviceも、`config/config.yaml`の`applications.system_services`へ固定ID／unit／許可操作を明示し、`./deck.sh service`でroot所有allowlistを導入したものだけAppsから管理できる。Web processは非rootのままで、任意unit名や任意systemctlは実行しない。
TCP、HTTP status／本文、許可ルート内ファイル、processのヘルスチェックを設定すると、単なるPID存在ではなく`RUNNING / DEGRADED / FAILED`を判別できる。
待受ポートを持つアプリは「Web」から開き、複数ポートがある場合は初回に選択して記憶する。環境変数の秘密値は暗号化保存され、画面とログではマスクされる。

### Webターミナル

新規セッションはtmuxとして作成されるため、画面遷移、リロード、通信切断、ControlDeck再起動後も履歴とprocessを復元できる。
PCでは通常のwheel／keyboard、iPhoneでは補助キー、コピー／貼り付けsheet、terminal面の上下swipe、右端overlay履歴バーを使う。
右端バーはtapで対応位置へ移動、dragで連続移動し、その操作だけではIMEを開かない。長文pasteはchunk ACK、再接続差分resume、hash検証可能な全量送信を使い、送信中もcancelできる。

### リモートデスクトップ

このPCを操作する場合は`./deck.sh enable-desktop`でヘッドレスRDP環境を登録し、「Remote」から接続する。外部のRDP／VNC／SSH接続も登録可能。guacdが正常でもローカルxrdpの3389番が停止している場合は画面上で区別して表示し、SSHから実行する復旧コマンドを案内する。
iPhoneでは1本指をtouchpad移動、tapを左click、長押し移動をdrag、2本指tapを右click、2本指上下をscroll、3本指tapをsoftware keyboardとして扱う。
接続情報のpasswordは暗号化し、guacd tunnelとWebSocketの双方で認証／Originを検証する。

### AIアシスタント

「AIアシスタント」でendpointとmodelを選び、通常チャット、添付資料を使う質問、ワークフロー生成、Deep Researchを実行する。
会話とjobはサーバーへ保存されるため、ブラウザを閉じても生成を継続し、再接続後にprogress、thinking、本文、usageを復元できる。不要なjobはcancelできる。
ワークフロー生成は要求からJSON Schema準拠定義を作り、構造／意味検証と品質スコアを通してから登録する。

### Deep Research

Deep Researchは単発検索要約ではなく、計画→サブ質問→反復検索→coverage再評価→不足query追加→引用検証→章分割レポート生成を行う。
検索深度はquick／standard／deep／exhaustiveから選び、round、検索回数、根拠文字数、最終tokenをcustom設定できる。

資料源はSearXNG Web／PDF、直接URL、OpenAlex・Crossref・arXiv等の学術情報、GitHub repository、添付／Knowledge RAG、許可ルート内ローカルコード、特許、SEC等の市場情報から選択する。
ローカルコード調査はsymlink、秘密ファイル、依存物、cacheを除外し、静的索引だけを作ってコードを実行しない。SearXNGは`./deck.sh searxng`で導入できる。
結果には引用ID、source一覧、coverage、未解決点、引用整合性が残る。速い概観はquick、設計／競合／技術選定はdeep以上が目安。

### ModelとKnowledge／RAG

「Model」ではOllama、llama.cpp、外部OpenAI互換providerを共通capability付きAPIで横断し、modelの検出、pull、load、unload、削除、個別／既定設定を行う。providerが対応しない変更操作は明示的に拒否される。
llama.cppはGGUFごとに独立systemd user unitを持ち、Context、batch、K/V cache、GPU offload、MTP、MoE、sampling、通常／Deep Research用CTXを個別設定する。
VRAM不足を避けるため、まず小さいCTXでloadを確認してから拡張する。AMD対応環境では電力／clock profileも選べる。

「Knowledge」ではcollectionを作り、text、URL、fileを取り込む。recursive／fixed／sentence／paragraph／Markdown／parent-childのchunk方式と、vector／full-text／hybrid／graph検索を選ぶ。
日本語や固有名詞はhybrid、関係性探索はgraph、質問の言い換えが必要ならHyDE／multi-queryが有効。検索testで根拠を確認してからLLMやworkflowへ接続する。

### ファイル、GitHub、電源、セキュリティ

ファイル画面は許可ルート内の閲覧、再開可能upload、download、text編集、preview、ごみ箱を提供する。削除はまずごみ箱へ移し、完全削除だけを破壊的操作として確認する。
GitHub画面はclone、pull、commit、履歴からのrevert、登録削除を扱う。private repositoryは`gh` device flowで認証し、tokenを画面へ貼り付けない。

電源操作は再起動／shutdown／予約／取消を提供し、予約はsystemd user timerへ永続化する。重要操作は監査ログへ記録される。
administrator／operator／viewerのRBAC、HttpOnly session、CSRF、Origin検証、TOTP、session失効を組み合わせる。外部公開は避け、Tailscale／WireGuard内での利用を推奨する。

### PCとiPhoneでのナビゲーション

PCは左sidebarと`Ctrl/Cmd+K` command palette、iPhoneは下部navigationとbottom sheetを使う。主要操作は原則2step以内、touch targetは約44px、Safe Areaと320px幅に対応する。iPhoneの下部navigationはSettingsの「Bottom Navigation」で最大6機能を有効化・並べ替えでき、Moreは常に右端へ固定される。設定は端末ごとに即時保存されるため、PCとスマートフォンで別の操作配置を使える。
入力時にiOS Safariが自動zoomしないfont size、sheetの`100dvw`上限、reduced motion、dark／light themeを共通適用している。

## ワークフローの基本的な使い方

ControlDeckのエディタは「配置して保存」で終わらず、入力→安全確認→テスト→観測→部分再実行→回帰テスト→公開を
1つの開発ループとして扱う。

1. ワークフローを作り、トリガーノードで入力名・型・必須・初期値・説明を定義する。新規ノードには安全に決定できる推奨初期値が入り、迷った場合は設定上部の「推奨値を適用」で空欄だけを補完できる。
2. ノードを接続する。主要入力が空なら直前ノードの代表出力が自動提案される。設定欄の変数ピッカーでは、直前／その他の上流を変数名・型で検索し、直近サンプルを見ながらカーソル位置へ`{{上流ノードID.フィールド}}`を挿入できる。
3. 「確認・テスト」を開き、入力値を設定して「実行前チェック」を行う。この段階ではexecutor、LLM、外部通信、書込み、secret復号を行わず、公開可否まで確認する。
4. 「下書きをテスト」へ切り替える。共通の実行前チェック後にdraftを実際に動かし、同じ画面で型付き最終出力と各ノードの結果を確認する。副作用は実行されるが、公開版は変更しない。
5. ノードの「実行」タブで、最新成功実行・指定実行・手動JSON・固定データを入力源にして単体実行する。上流を再計算せずに問題を切り分けられる。
6. 必要なら「このノードまで実行」「このノードから再実行」を使う。途中再実行は当時版／現在版を選択できる。
7. 実行履歴でノードを選び、実入力・実出力・時間・retry・token・ログ・エラー・artifactを確認する。タイムラインでは並列実行と最長ノードを把握できる。
8. 入力と成功結果を回帰テストケースとして保存し、変更後に一括実行する。期待値との差はpath・期待値・実値で表示される。
9. 主ボタンの「更新して開く」を押す。保存と公開前検証を行い、問題がなければ最新内容を公開して「Play」画面を開く。公開内容と編集中内容が同じ場合は「アプリを開く」と表示され、versionを増やさず直接開く。
10. 入力フォームから公開版を実行し、同じ画面で進捗、承認、結果、最近の実行を確認する。エディタ内で直ちに実行してノード状態まで観測する高度な操作は「その他 → 公開して直接実行」を使う。

各ノード設定では、必須表示、推奨値、その値を勧める理由、最短手順、主な入出力、副作用、構成例をその場で確認できる。
外部URL、ファイルpath、model、Secretなど環境ごとに異なる値は自動生成せず、ユーザーの既存値も推奨値適用では上書きしない。

`llm.chat`は`auto_load=true`が既定である。ControlDeck管理中のOllamaは生成前にmodel load APIを呼び、llama.cppは登録済みinstanceを
systemd user unitで起動して`/health`完了まで待つ。事前起動は通常不要で、待機中はノード進捗へ表示する。既定の起動待ち上限は240秒で、
ノードの`startup_timeout`から10〜600秒へ調整できる。外部OpenAI互換endpointはControlDeckから起動せず、従来どおり接続先側で管理する。

実行前チェックとdraftテストの両方で「構造上は実行可能」と「公開できます」を分けて表示する。「更新して開く」と「公開して直接実行」も同じ判定を使い、
最終出力不足、出力名重複、未登録secret、固定データ、未合格回帰テストを具体的な修正案付きで示す。HTTP 409は単なる番号ではなく、
阻害理由を画面へ表示する。

### サンプルブック

サンプルブックの全ワークフローは、コピー直後の実行前チェック・公開前検証・公開を自動テストしている。LLM、RAG、Webhook、
管理対象アプリなど環境依存ノードを実行する場合は、各サンプルの「事前準備」に従ってendpoint、model、通知先、対象アプリを確認する。
公開自体に必要な正式な型付き出力契約は、すべてのサンプルに含まれる。

「受注データ分析」サンプルは外部サービスを使わず、JSON入力 → 金額filter・重複除去・sort → 地域別sum集計 → Table／JSON tree／Metricの
並列出力までを一度に確認できる。入門用の直列フローだけでなく、サイト監視、アプリ復旧、Deep Research、RAG、定期論文収集など、
分岐・副作用・エラー設定・複数output contractを組み合わせた実用例も収録している。

### 公開ワークフローを「Play」で使う

左メニューまたはiPhone下部ナビの「Play」は、公開済みワークフローを業務アプリのように使う実行専用画面である。内部route `/runner` とAPI上のWorkflow Runner名は既存リンクとの互換性のため維持する。
公開アプリを選び、生成された入力フォームへ値を入れ、同じ画面で状態、承認、型付き最終出力、最近の実行を確認できる。`human.approval`で待機すると承認文、担当者、期限、承認／却下を表示し、キャンバスへ戻らず処理を再開できる。
ワークフロー一覧では公開履歴がある項目に「公開版を開く」を表示する。編集中の差分があっても既存公開版は維持され、エディタの「更新して開く」を明示的に押すまで本番版は変わらない。選択中のworkflow IDはURLへ保存されるため、リロードやホーム画面からの再起動でも同じ公開アプリへ戻れる。
過去入力は「入力を再利用」でフォームへ戻せる。キャンバス、ノード、接続、config、definition/runtime snapshotは公開アプリAPIから返さない。

`operator`など`workflows.run`だけの利用者は公開アプリを使用する。draft、ノード実値、途中再実行、version差分等の開発情報は
`workflows.edit`を持つ利用者だけがエディタとデバッグAPIで参照できる。公開アプリは常にimmutableな公開版を実行し、draft変更は再公開まで反映しない。

### App Studio（Application Builder F1）

ワークフローの「その他 → アプリ化」から、Workflow DefinitionをportableなWorkflow IRへ変換し、独立したApplication Spec v1 Projectを作成できる。
UI上の「App Studio」ではtrigger input、typed output、node/edge、capability、side effect、target互換性、Application page/entity/API/targetの件数、
blocking/warning/suggestionを確認する。Application Spec、型、framework/node capabilityはバックエンドschema/registryを正とし、未知fieldを保存時に失わない。

F1ではbackend catalogを正とするSemantic Component Editorを提供する。Pageを作成し、Stack／Grid／Card、入力、表示、実行button、table、chartを追加して、Component Tree、Inspector、Desktop／Tablet／Mobile Previewから同じApplication Specを編集できる。Desktopではcontainerへのdrag、touch／keyboardでは明示的なMove操作を使い、Undo／Redo後に保存できる。Workflow接続ProjectからはOpen Workflowで処理設計へ戻れる。

保存済みDesignでは「Review Patch」から構造化JSON Patchを読み込み、変更operationを個別に選択して、選択した差分だけをbackendで再検証できる。Before／AfterのPage・Component数、structured diagnostic、Spec checksumを確認した後、有効な差分だけを原子的に適用する。選択を変えた場合はPreviewを無効化して再検証を要求し、別画面でSpecが更新された場合はstale checksumとして停止する。

Inspectorのstructure／binding／style／position／content lockは、今後のAI再設計と現在のPatch Reviewの双方に適用される。たとえばcontentを固定した部品の表示値変更や、bindingを固定した部品の接続変更はPreview段階で拒否される。ロックはユーザーの直接編集を禁止するものではなく、AI提案が保護境界を越えないための指定である。

「AI Design」はModelsで検出したOllama／llama.cpp／LM Studio等のOpenAI互換modelを利用し、要望、対象範囲、Preserve／Balanced／Redesignを指定してSimple／Balanced／Denseの3案を生成する。LLMへ自由なGUI source codeを書かせず、redact済みApplication Spec、backend catalogのSemantic Component、Design Token、Binding Sourceを渡し、返却を厳格なPatch schemaへ限定する。各案は生成直後にlock・schema・binding・secret・target検証を通り、利用者が選んだ案もF2.2の差分画面で確認してから適用する。

生成アプリのLLMはApp Studioへ組み込む必要がない。`LLM Runtime`で`None`または`External provider · not bundled`を選び、Ollama、LM Studio、OpenAI互換endpointへ接続できる。外部方式は`LLM_BASE_URL`／`LLM_MODEL`の環境設定だけを生成契約に持ち、model、runtime binary、API keyをSpecや成果物へ含めない。Embedded RuntimeとRemote ControlDeckは後続generator Phaseまで選択不可として表示する。

source生成、build、package、artifactはまだ提供しない。AI GUI設計はApplication Spec Patchの提案までであり、生成codeやbuildが成功したように見せるdummy操作は置かない。
後続PhaseでC# Console、ASP.NET、GUI/DB、構造化AI提案の順に実装する。LLMは自由なGUI codeではなく、検証可能なApplication Spec Patchだけを提案する。

ノードmetadataは推奨初期値とその理由、詳細help、変数picker対応を返す。危険な対象値を除いて新規ノードへ初期投入し、
入力欄から型の合う上流変数を検索・sample確認・カーソル位置へ挿入できる。frontendへnodeごとの推奨値を二重定義しない。

### Project Lab（成果物previewと永続CLI/test/Web実行）

「Project Lab」は`~/CodeDEV`直下のPython、Node/Vite/React、静的Web、CMake、Rust、.NET projectを自動検出し、開発成果物をまとめて評価する画面である。
HTML、画像、CSV/TSV、JSON、Markdown、PDF、audio/video、log/textを型別previewし、Git branch・dirty状態と明示manifestのprofileも確認できる。

任意の`.controldeck/project.json`では`cli`、`web`、`static_html`、`test`、`artifact` profileを宣言できる。commandはargv配列、cwdとartifact globはproject内相対path、秘密値は`secret_refs`の名前参照だけを許可する。
自動検出だけを理由にprogramを起動することはない。CLI/test/Webはmanifestで明示したprofileをユーザーが操作した場合だけ起動する。LLM評価は後続Phaseで追加する。

CodeDEV外path、symlink escape、秘密file名、`.env`、source code、`node_modules`等は成果物previewから除外する。HTMLは認証付き同一origin配信、CSP、script無効のsandbox iframeで表示し、JSON/CSV/text previewの秘密らしいfieldを伏せ字化する。

`.controldeck/project.json`へ`cli`、`test`または`web` profileをargv配列で定義すると、Project Labから明示実行できる。実行はブラウザ接続から独立した制限付き`systemd --user` unitとなり、状態、終了code、経過時間、redact済みlog、実行後に生成・変更されたartifactを同じ履歴で確認・停止できる。Web profileではcommandの`{host}`/`{port}`を自動割当に置換し、unitのprocess treeがlocalhost portを実際にLISTENした後だけsandbox iframeへproxyする。Secret参照profile、shell文字列、project外cwd、未許可binaryは安全なcredential/adapterが実装されるまで実行しない。

### 型付き最終出力 `output.render`

新規フローの最終段には`output.render`を推奨する。旧`signal.display`は既存定義との互換用として継続利用できる。
出力名、タイトル、説明、値、renderer、schema、ファイル名、MIME type、コピー／ダウンロード／折り畳み／機密指定を持ち、
手動実行・API・schedule・chat・サブフローで共通の`name / type / value`契約を返す。編集デバッグAPIだけは追跡用`source_node_id`も返す。

主なrendererはAuto、Plain text、Markdown、JSON tree/raw、Table、Key-value、Code、Image/Gallery、Audio、Video、
File、Link、Status、Metric、Progress、Citation list。JSON・Table・Key-value等はJSON文字列を型付き値へ変換する。
`sensitive`を有効にした値は実行中の後段参照には使えるが、履歴・DB・API応答へ保存するときに`***`へ置換される。

### draft、公開版、固定データの違い

- draft: エディタで自動保存される開発中定義。「下書きをテスト」、単体実行、回帰テストが使用する。
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
./deck.sh database status  # 本体DBの接続先と疎通を秘密値なしで確認
./deck.sh database postgresql # PostgreSQL URLを非表示入力して切替
./deck.sh database sqlite  # 既定SQLiteへ復帰（PostgreSQL設定は0600で退避）
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

Control Deck本体DBは既定SQLiteに加えてPostgreSQLへ切り替えられる。`database postgresql`は接続を先に確認し、
現行SQLiteをbackupしてから、credentialを`config/database.env`（実行ユーザー所有・0600）へ保存する。
systemd unit本文、YAML、ログにはURLを展開しない。起動時Alembic migrationに失敗した場合は直前のDB設定へ
自動復帰する。既存SQLite dataのPostgreSQLへの自動移送は行わないため、空databaseまたは別途検証した移行を使う。
PostgreSQL利用時の`deck.sh backup`／`restore`は`pg_dump`／`pg_restore`のcustom archiveを使い、接続passwordを
argvへ含めない。非対話設定では`CONTROL_DECK_POSTGRES_URL`を一時環境変数として指定できる。

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
- [公開アプリ（Workflow Runner）/ Project Lab 詳細設計](docs/design-workflow-runner-project-lab.md)
- [Workflow Application Builder 詳細設計](docs/design-application-builder.md)

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
