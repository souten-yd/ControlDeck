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
- 15秒間隔および手動のヘルスチェック（プロセス / TCP / HTTP status・本文 / 許可ルート内ファイル / 固定catalogの許可コマンド）、失敗時DEGRADED
- ✅ 既存system serviceはroot所有catalogの固定ID／unit／start・stop・restartだけを最小特権helperで操作。Webは非root、任意unit／action／shell禁止、kill禁止、監査、320px／PC catalog選択UI

### Phase 3 — 監視
- CPU / RAM / ディスク / ネットワーク / 稼働時間（psutil）
- GPU / VRAM（AMDは低負荷sysfs fast path → amd-smi / rocm-smi fallback、NVIDIAはnvidia-smi、失敗時 N/A）とsystemd process tree別のDRM GPU / VRAM使用量
- 消費電力推定、単一メトリクス WS ストリーム、履歴（生 24h / 1 分平均 30 日）
- アラート（しきい値 + 通知センター）
- 電源予約は予約時だけ systemd ユーザーtimerを生成（Web再起動・SSH切断後も継続、期限切れは再実行せず、取消時削除）

### Phase 4 — ファイル + ターミナル
- 許可ルート限定ファイルマネージャー（一覧 / 再開可能チャンクアップロード / ダウンロード / 編集 / コピー / 移動 / ごみ箱 / ZIP・tar.gz圧縮・安全展開）
- ごみ箱（復元 / 完全削除 / 保持期間 / 容量上限）とアップロード進捗・中止・再開
- テキスト／Markdown／JSON／YAML／画像に加え、Range対応PDF／音声／動画preview。PC右panel／mobile bottom sheet、Monaco遅延ロード
- Web ターミナル（xterm.js + tmux、モバイル補助キーバー）

### Phase 4b — Terminal Snippets / Durable Automation（2026-07-20 完了）

1. ✅ 共通Snippet CRUD、parameter template、複数選択／順序付きcompose、展開preview、監査
2. ✅ 既定のDetached runをsystemd user transient serviceで実行し、許可rootのrealpath、固定環境、timeout／resource／log上限、durable run状態を実装
3. ✅ tmux session送信は明示的な上級modeとし、session ID、shell待機／program一致condition、bracketed paste、condition不一致skipを実装
4. ✅ 1回／毎日／毎週／隔週、timezone、catch-up、次回時刻を持つschedule CRUDとschedule別systemd user timer、再起動継続、run historyを実装
5. ✅ Terminalの共通`Snippets`入口＋各cardの3点menu、PC side panel／mobile bottom sheet、Library／Compose／Scheduleの段階開示、状態indicator、320px／PC E2E

### Phase 4c — Terminal V2 並行再設計（2026-07-20 開始）

現行UI／操作は契約として保持し、新しい接続／履歴／入力／geometryコアを別系統で実装する。
既定はV1のままとし、専用sessionのLab検証、canary、V2既定化、V1ロールバック期間の順に進める。
同一tmux sessionのV1/V2同時接続はサイズ/redrawが干渉するため禁止する。詳細と入れ替え合格基準は
[`design-terminal-v2.md`](design-terminal-v2.md)を正とする。

1. ✅ UI契約、責務分割、Lab/canary/ロールバック、合格基準、非本文telemetry境界を固定
2. ✅ V2 connection/renderer/input/geometry/historyコアと専用sessionのLab切替
3. ✅ V1と同じUI契約、Paste／Copy／helper／swipe／Automation／session switchの実装
4. ✅ 320／390／768／1280px自動回帰、履歴／入力／keyboard／scroll／reload計測
5. ⬜ 物理iPhone Safari／PWA確認
6. ⬜ canary後にV2既定化、V1即時復帰確認、ロールバック期間後の整理

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
2. 再現性: ✅ published version、execution snapshot、node run、test case、pin、retry/resume、✅ durable sequence event replay／認証SSE／Execution Debugger接続、✅ Alembic baseline／既存SQLite backup・checksum・read検証／schema drift停止、✅ DB-backed durable pause／再起動継続／schema入力、✅ checksum付きartifact offload／認可download／削除清掃
3. typed output / node / error route: ✅ output.render、✅ durable approval／human.form／merge、✅ data nodes、✅ typed error／timeout route、✅ flow.return／flow.error／flow.note／test.assert、✅ durable control.delay、✅ published-subflow control.try、✅ GPU／VRAM／disk／llama-server／systemd／file system trigger、✅ Workflow-scoped durable queue、✅ TTL cache、✅ typed/versioned durable state、✅ durable Workflow business event outbox／published custom event trigger／再起動再送、✅ version-pinned typed `flow.map`／ordered result／cycle preflight、✅ ordered `data.batch`、✅ durable shared rate limit、✅ CLOSED／OPEN／HALF_OPEN circuit breaker、✅ sample／全node詳細docs／指定E2E flow
4. ✅ large flow: group/collapse、既存`flow.call`／`control.try` subflow、outline/search、fit selection、worker layout、quick add、typed edge metadata、50段undo/redo、1.2秒autosave＋optimistic conflict、100-node編集／500-node read-only navigation
5. ✅ AI: redacted diagnose／versioned operation patch preview・選択適用／baseline test生成／live runtime route／Project Intelligence
6. sample/docs: ✅ 19の実用sample、✅ 全62 node詳細説明、✅ Time Travel／Local LLM Route／PC State Recovery／AI Patch／Regression Batch回帰E2E
7. App Studio F3: ✅ F3.1 Design Token／Composite／Pattern、✅ F3.2 property schema／全状態preview／a11y、✅ F3.3 Grid／Table／Chart editor、✅ F3.4 Binding／Event editor、✅ F3.5 Visual Preview Diff／3案比較、✅ F3.6 Parameterized Template、✅ F3.7 focus／keyboard／contrast audit。F3完了
8. Application Builder Phase B: ✅ B1 Platform Advisor／framework・host matrix／複数target override／副作用なしPreflight、✅ B2.1 core deterministic C# Console generator／manifest／source ZIP／self-test、✅ B2.2 condition branch／merge／dead-edge／retry・timeout runtime、✅ B2.3 named variable／pure data runtime、✅ B2.4 nested count／foreach loop runtime＋実net8.0 build
9. Application Builder Phase C1: ✅ typed API endpoint／background job contract、route・Workflow参照・anonymous・schedule validation、✅ deterministic ASP.NET health／OpenAPI 3.1／sync-async Workflow endpoint／status・SSE・cancel、API key、Dockerfile、self-test、実net8.0 build
10. Application Builder Phase C2: ✅ dependency-free JSON Schema request／sync-async response runtime、OpenAPI schema、✅ manual／interval／daily／5-field cron、time zone／DST、overlap policy、atomic durable state、crash pending recovery、実net8.0／Kestrel／320px E2E
11. Application Builder Phase D1: ✅ typed Entity／field／relation／CRUD contract、✅ transactional SQLite WAL additive migration／incompatible change stop／durable delete audit、✅ authenticated parameterized CRUD／OpenAPI、決定的15／16-file source、実net8.0／Kestrel／320px E2E。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持し、全言語対応は要求しない。次はD2 Entity editor／CRUD Table binding（GUI sourceはE〜G2）
12. Application Builder Phase D2: ✅ responsive Entity／field／relation／CRUD editor、✅ Designと共通の50段Undo／Redo・単一Spec save、✅ typed Entity／field bindingとbackend参照diagnostic、320px E2E。次はE1 Semantic Component／Entity bindingのASP.NET Blazor GUI source
13. Application Builder Phase E1: ✅ deterministic Blazor static SSR Page／responsive Semantic Component source、✅ Entity collection Data TableとD1 list CRUD binding、✅ escape／`textContent`限定、unsupported action・event・認証のblocking diagnostic、実net8.0／Kestrel／320・1280px Chromium。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はE2 form／CRUD mutation／browser認証adapter
14. Application Builder Phase E2: ✅ Data Table create／update／delete typed propertyとEntity CRUD公開範囲diagnostic、✅ schema-driven mutation form／More menu／Delete確認、✅ API-key→12時間memory-only HttpOnly session、CSRF header、login rate／capacity／HTTPS境界、CSP、実net8.0／Kestrel／320・1280px Chromium。次はE3 Workflow trigger form／result renderer／navigation event
15. Application Builder Phase E3: ✅ Workflow binding→保存済み同期API endpointの一意解決／blocking diagnostic、✅ JSON Schema string／enum／integer／number／boolean／object／array form、✅ `textContent`限定typed result、✅ success／errorの既存Page固定navigation、実net8.0／Kestrel／320px Chromium。typed state consumerがないstate-set／再帰Workflow eventは未対応のまま明示停止。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped client state／query binding contract
16. Application Builder Phase E4: ✅ typed client state宣言／初期値／容量・型検査、✅ Text／Markdown／Metric／Text Input consumer、input change／Workflow success・errorのstate-set、✅ memory-only runtime／reload reset／`textContent`限定、✅ Create／Target／Export／Reviewの目的別workspace、Canvas／Data、PC 3ペイン／mobile bottom sheet、単一Save、実net8.0／Kestrel／320・1280px Chromium。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped query binding contract
17. Application Builder Phase E5: ✅ typed Entity collection query／list公開・consumer・column検証、✅ Query Editor／stable ID／単一limit・cache設定、✅ loading／not-loaded／empty／固定error／Refresh、memory cache／同時取得共有／mutation後invalidate、実net8.0／Kestrel／320・1280px Chromium。既存`entity:` bindingは後方互換として維持する。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はAPI query sourceとfilter／sort／pagination contract
18. Application Builder Phase E6: ✅ Entity/API collection queryのtyped source、固定API input／result path／request-response schema検証、✅ field型別filter operator／値型、最大20 filter／3 sort、offset pagination、✅ Query Editorのsource別段階開示、✅ whitelist column＋全値parameter bindingのSQLite query、決定的sort、固定400、✅ browser Previous／Next／API POST／nested collection、実net8.0／Kestrel／320・1280px Chromium。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はSecret injection／side-effect nodeの安全な生成境界と隔離build
19. Application Builder Phase E7／B2.5: ✅ Secret名をopaque aliasへ変換し、値をsource／manifest／logへ含めない環境変数注入、欠落・64KiB上限・最終出力／file write redaction、✅ 固定HTTPSまたはloopback HTTP、redirect／cookie無効、header・body・response上限、credential位置制約、内容を含めない監査、✅ application-owned root相対file read／write／exists／glob、containment／symlink拒否、atomic overwrite／append・scan上限、✅ ASP.NETはapi-key必須・anonymous endpoint拒否、Console／ASP.NET共通runtime、実net8.0／Kestrel／320・1280px Chromium。source生成自体は引き続きI/O・Secret解決なし。次はsystemd user transient unitを用いる隔離build
20. Application Builder Phase B3: ✅ 保存済みSourceの再生成／ZIP containment、✅ allowlist .NET SDKとvenv worker、✅ network deniedをIPv4／IPv6でfail-closed、最小SDK環境、systemd user transient unit、resource／timeout／同時実行制限、✅ durable phase／cancel／interrupt／bounded redacted log／artifact checksum・download・delete／監査、✅ App Studio Build & test／phase indicator／Cancel／log／artifact／Delete、実C# Console・ASP.NET Core self-test、320・1280px Chromium。Linux／Windows向け生成対象は2系統を維持する
21. App Studio Workflow自動アプリ化: ✅ Workflow trigger／output契約からtype-aware入力、同期endpoint、型付き結果、navigation、responsive Pageを含む動作保証baselineを作成時に自動適用、✅ Canvas上のAdvisor根拠／契約form preview／生成・動作確認の主導線、✅ Workflow契約を明示したAI再検討3案とvisual diff／静的検証／選択適用、✅ Canvas Inspectorによる任意修正、実Ollama 3案、実net8.0 build／self-test／Kestrel、320・1280px Chromiumで入力→Workflow→結果を確認。AI／modelが利用不能でもbaselineは完全動作し、AI案は既存動作を壊す自動適用をしない

運用導線は`Workflows`へ統合する。`workflows.edit`利用者には編集対象、`workflows.run`だけの利用者には公開済み対象を同じ一覧入口から示す。
エディタの主操作`Run`は差分保存・公開検証・必要時だけversion更新・実行を行い、同じエディタ内のdebug panelへ結果を表示する。
公開実行面は一覧項目の`Open App`から開き、`/runner?workflow={id}`は公開版専用APIを使う互換deep linkとして維持する。

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
