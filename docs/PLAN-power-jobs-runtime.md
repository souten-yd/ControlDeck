# 大規模計画：PSU電力/電気代 + サーバー主導ジョブ基盤 + llama.cpp/OpenCode

> このドキュメントは大規模改修の設計・進捗・**Codex 等への引き継ぎ**用。
> セッションのトークン制限で中断した場合、ここを起点に再開すること。
>
> Model runtime、モデル別設定、独立AIアシスタント、生成遅延/失敗の詳細設計は
> [`design-model-runtime-assistant.md`](design-model-runtime-assistant.md) に統合した。
> ターミナルのモバイルキーボード追従とPC/モバイル共通の長期履歴は
> [`design-terminal-mobile-history.md`](design-terminal-mobile-history.md) に統合した。

## 進捗サマリ（随時更新）

| 計画 | 状態 |
|---|---|
| A. PSU電力監視 + 電気代（起動中/日/月） | ✅ 完了（実機検証済み・マージ済み） |
| B. サーバー主導ジョブ基盤の汎用化 | ✅ 完了（owner・冪等性・heartbeat・priority・全体WSを再実装/検証） |
| C. 永続チャット（ブラウザを閉じても回答生成・復元） | ✅ 完了（会話一覧/切替/改名/削除と生成checkpointを実装/実機検証） |
| D. ワークフロー生成の意味検証・品質スコア | ✅ 完了（厳格schema・副作用なしdry-run・実機生成を検証） |
| E. LLMランタイム抽象（Ollama/llama.cpp provider） | 🚧 provider検出・共通モデル操作/health/全runtime同時load上限まで完了（生成stream/cancel契約が残る） |
| F. llama.cpp 導入（Vulkan/ROCm・systemd・MTP・思考深度） | ✅ 複数GGUF catalog/instance・個別unit・自動起動/idle制御まで完了 |
| G. OpenCode オプトイン統合（feature registry・プラグイン境界） | ⬜ 未着手 |
| H. ワークフローノード超強化（型/capability/dry-run/新ノード） | 🚧 metadata/型/capability/dry-run完了（検索/お気に入りと追加ノードが残る） |

---

## 2026-07-15 再監査と完了設計（完了表記を信用しない再評価）

### 実装照合結果

コード、DBモデル、API、UI、テストを相互照合した。旧「既存資産」の記述には、すでにDB化したジョブや
永続チャットを「未永続」とする古い説明も残っていたため、進捗サマリは実装事実に合わせて修正した。

| 領域 | 実装済みの根拠 | 完了を妨げる不足 |
|---|---|---|
| A 電力 | PSU sysfs、日/月/起動セッション積算、欠測処理、永続化テスト | なし（実機確認済み） |
| B ジョブ | `Job` DB、`JobControl`、owner、冪等キー、heartbeat、安定priority queue、queued/running cancel、全体WS | なし（再起動時queued/running→interruptedを含め検証） |
| C チャット | Conversation/Message DB、サーバージョブ、再接続、会話一覧/切替/改名/削除 | なし（生成本文は1秒checkpoint、ワークフロー生成結果はjob resultへ永続化） |
| D 生成品質 | semantic check、quality score、自動修正、LLM JSON Schema出力、副作用なしdry-run | なし |
| E provider | 共通catalog、capability、list/load/unload/delete adapter | providerの型付き契約、install/start/stop/health/stream/cancelの共通実装がない |
| F llama.cpp | 導入、backend切替、systemd、型付きMTP/KV/MoE/cache/context/sampling設定 | モデル別複数GGUF catalog/instanceがない |
| G OpenCode | なし | feature registry、deck.sh操作、未導入時の未登録境界、code.agentが未実装 |
| H ノード | v2 DAG、retry/cancel、36種node、backend metadata/capability/型、共通dry-run | 検索/お気に入り、progress対応node、計画記載の一部便利nodeがない |

### Web軽量化の実測

- 外向きの高周波pingはない。watchdogの15秒通知はローカルsystemd notify socketであり通信ではない。
- ダッシュボード実測（12秒）: metrics WS 1接続/6受信、`GET /apps` 3回、その他初回REST各1回。
  `/apps` は平均28.7ms、最大39.1ms。WSは意図した2秒更新を満たす。
- Webプロセス待機負荷（10秒平均）: CPU 1.6〜1.7%、自発context switch 52〜60回/秒。
- 主因は `amd-smi metric --json` の2秒ごとのプロセス起動。実機で1回40〜60ms CPU、最大RSS約25MB、
  JSON約23KBだった。GPU/VRAM/温度/電力は同じamdgpu sysfsから取得可能。

### 詳細設計: 通信・処理最適化

機能の鮮度を落としすぎず、バックグラウンド負荷と不要通信を分離して削減する。

1. **GPU fast path**: AMDは外部CLIより先にsysfs providerを選ぶ。複数GPUではVRAM総量が最大のdeviceを
   primary GPUとし、busy/VRAM/hwmon温度/電力を直読する。必要な主要値が取れない場合だけ
   amd-smi→rocm-smiへfallbackする。NVIDIAは現状互換を維持する。
2. **画面polling**: metricsは単一WS・2秒周期を維持する。アプリ状態は共有TanStack Queryのまま15秒周期へし、
   start/stop/restart/kill後は楽観更新+即時invalidateで操作応答性を維持する。非表示タブでは停止する。
   model jobは全体job WSでquery cacheを更新し、idle時2秒pollを廃止する。
3. **ジョブ通知**: `Job.log/set_progress/status` をrevision付き通知へ集約し、0.4秒sleep pollingを廃止する。
   認証済み `WS /jobs/stream` は接続ユーザーが閲覧可能なジョブのsnapshot/updateだけを配信する。
   過負荷時はbounded queueの古い中間更新を捨て、最終状態を必ず再取得できる設計とする。
4. **性能受入条件**: 同一実機・アイドル時10秒平均でWeb CPUを変更前比50%以上削減、外部AMD監視CLIの
   周期起動0回、ダッシュボード30秒の`/apps`を初回込み3回以下、metricsは15±1 frame/30秒、
   タブ復帰・アプリ操作・ジョブ進捗の機能を維持する。

### 詳細設計: ジョブ基盤完了

- 既存`jobs`表は互換維持し、追加制御情報は新規`job_controls`表（job_id PK/FK、owner、idempotency_key、
  priority、heartbeat_at、revision）へ置く。既存行はownerを`jobs.owner_user_id`からfallbackする。
- `(owner_user_id, kind, idempotency_key)`を一意にし、同一要求は既存running/succeeded jobを返す。
  failed/canceled/interruptedの再試行は新規jobを作り、以前のcontrolを履歴として残す。
- priorityは高い値を先に実行する安定PriorityQueue（同値は作成順）で扱い、同時実行数を制限する。
  cancelはqueued/running両方を扱う。heartbeatはprogress/eventと定期touchで更新し、stale判定を可能にする。
- list/get/cancel/WSはownerを強制する。管理者であっても通常APIは他ユーザーの生成内容を返さず、
  ownerなしのシステムjobだけ共通表示する。破壊的cancelは監査する。
- サーバー再起動時はrunningだけでなくqueuedもinterruptedへ遷移し、DBに最終状態を残す。

### 実装・PR分割

1. GPU/sysfs fast path、polling削減、変更前後計測。
2. ジョブ所有者分離、通知WS、冪等性/heartbeat/priority、UIのjob WS移行。
3. C/Dの残件（会話picker、gen checkpoint、schema/dry-run）。
4. E/Fの共通runtime契約とllama詳細/複数instance。
5. Gのopt-in registry/OpenCode境界。
6. Hのmetadata/型/dry-run/UI/不足ノード。

各PRでbackend test、frontend build、実サービスAPI、1280px/320pxを確認し、
`docs/implementation-status.md`へ測定値と残件を更新する。完了条件を満たさない項目は✅へ戻さない。

---

## 既存資産（再利用前提。重複実装を避ける）

- **メトリクス収集**: `backend/app/monitoring/collector.py` の `MetricsCollector`。
  - `_collect_once()` が約2秒周期（`config.monitoring.interval_seconds`）。RAPL(CPU)/GPU/hwmon を読む。
  - `run()` ループ、`_flush_minute()`（60秒毎にDB保存）、`subscribe/unsubscribe`（WS配信）。
  - snapshot の `"power"` = `{cpu_watts_estimated, gpu_watts, total_watts_estimated, is_estimate}`。
- **API**: `backend/app/monitoring/router.py` の `GET /api/v1/system/overview`、`WS /api/v1/system/metrics/stream`。
- **ジョブ基盤**: `backend/app/jobs/service.py`（メモリ + `Job` DB、再起動時interrupted化、一覧/詳細/cancel）。
  再監査で確認した所有者分離・制御metadata・通知方式の不足は計画Bの残件として補完する。
- **ワークフローエンジン v2**: `backend/app/workflows/engine.py`。並列DAG/join/リトライ/on_error/承認/flow.call/イベント・Webhookトリガー実装済み。`docs`不要。
- **チャット**: `backend/app/workflows/chat_persist.py`で会話/メッセージ/生成jobを永続化済み。
  `chat_router.py`の`/chat/build`もジョブ化済み。会話picker等は計画Cの残件として補完する。
- **LLM**: OpenAI互換 `/v1/chat/completions` 経由。think は Ollama ネイティブ `/api/chat` 経由（`_native_base`）。
- **設定**: `backend/app/config.py`（pydantic）、`config/config.yaml`、`config/config.example.yaml`。
- **暗号化**: `app/security/crypto.py`（Fernet）。**シークレット**は `WorkflowSecret`。
- **DBモデル**: `backend/app/models/__init__.py`。テーブル追加は `create_all` で自動。**既存テーブルへの列追加は不可**（マイグレーション無し）→ 新テーブルで対応。

---

## 計画A：PSU電力監視 + 電気代（完了）

### 実機
- `corsairpsu` は hwmon 番号可変（実測 hwmon6）。`/sys/class/hwmon/hwmon*/name` を探索。
- `power1_label = "power total"`, `power1_input`(µW)。62000000 → 62.0W。
- 電力は **PSUのDC総出力**。AC入力は `output/efficiency` で概算（効率既定0.85）。
- `liquidctl`の`Estimated input power`/`v_in`は使わない。

### 設計
- `backend/app/monitoring/psu.py` — `read_corsair_psu()`：hwmon動的探索→power total等を dict、無ければ `{"available": False}`。`sensors`/`liquidctl`のサブプロセスは使わず sysfs 直読み。
- `backend/app/monitoring/electricity.py` — `ElectricityAccumulator`：
  - `time.monotonic()` で台形積分。`delta_kwh = ((prev+cur)/2)*dt/3_600_000`（W・秒）。
  - boot_id（`/proc/sys/kernel/random/boot_id`）でセッション管理。boot_id変化で session リセット。
  - 日別（`electricity_daily`）/月別（日別のSUM）。日付境界で積分区間分割。タイムゾーンはOS/設定。
  - 異常間隔（>上限, 逆行, 欠測, サスペンド跨ぎ）は積算しない。
  - 保存：メモリ積算 + 600秒毎チェックポイント + 日/月境界 + 終了時(lifespan) + 単価/効率変更時。
- collector `_collect_once` で PSU 読取→accumulator.update→snapshot `"power"` に統合フィールド追加。
- DB: `electricity_daily`(local_date PK, energy_kwh, cost_yen, price_per_kwh_yen, sample_duration_sec, first/last_sample_at, updated_at)、`electricity_state`(boot_id, session_energy_kwh, last_input_power_w, last_sample_wall_time, updated_at)。
- config: `monitoring.electricity {enabled=true, price_per_kwh_yen=35.69, psu_efficiency=0.85, persistence_interval_seconds=600}`。検証: price>=0, 0.50<=eff<=1.00, 60<=interval<=3600。
- API `power` 追加: output_power_w, estimated_input_power_w, session/today/month energy+cost, price_per_kwh_yen, psu_efficiency, persistence_interval_seconds, last_persisted_at, vrm/case温度, fan, available, source。
- ホーム(Dashboard): PSU総出力 / コンセント側推定 / 起動中・今日・今月の電気代+kWh。取得不可(null)と0Wを区別。

### 完了条件A
- 実機で負荷変動に追従、起動中/今日/今月が増加、再起動で維持、OS再起動で起動中0、PSU消失で他監視継続。
- 単価 35.69円/kWh に統一（旧31円を残さない）。2秒毎DB書込しない（10分チェックポイント）。

---

## 計画B〜G：サーバー主導ジョブ基盤ほか（実装状況・残件設計）

### B. 汎用ジョブ基盤 ✅ 完了（2026-07-15再監査後に補完）
- **実装済み**: `jobs` テーブル（events_jsonスナップショット）と、互換性を保つ追加`job_controls`テーブル。
- メモリ(`_jobs`)=高速WSストリーム、DB(`Job`)=状態/進捗/結果/末尾50イベントを永続化。DB書き込みは作成・状態変化・終了の要所のみ（毎トークン書かない）。
- `job_controls`にowner/idempotency_key/priority/heartbeat_at/revisionを保存。同一owner/kind/keyのqueued/running/succeededを再利用し、失敗系は履歴を残して再試行できる。
- 最大4同時実行の安定priority queue（同値は作成順）。queued/running双方をcancelでき、定期heartbeatとevent/progress時revisionを記録。再起動時はqueued/runningをinterruptedへ移す。
- API: `GET /jobs`、`GET /jobs/{id}`、`POST /jobs/{id}/cancel`、`WS /jobs/stream`。owner本人とownerなしsystem jobだけをREST/WSで返し、cancelを監査する。
- 個別streamの0.4秒pollとModel画面の1〜2秒pollを廃止。通知Eventで待機し、全体WSは100ms coalesce後に最新revisionを配信する。
- **検証**: owner隔離、snapshot、冪等性、priority順、queued cancel、DB復元を自動テスト。実ブラウザ12秒でModelのjobs RESTは初回1回、jobs WSは1接続、横overflow/console errorなし。

### C. 永続チャット ✅ 完了
- **根本原因**（従来 /chat/stream）: WS ハンドラ内で LLM を直接 stream → 切断＝中断、回答はブラウザのみ保持・DB未保存だった。
- **実装済み**: `Conversation`/`ChatMessage` テーブル。`chat_persist.py` で送信時に user + assistant placeholder + chat.completion ジョブを DB 作成。ワーカーがサーバー側で生成し ChatMessage へ 1秒毎チェックポイント保存。WS(`/chat/messages/{id}/stream`)は通知のみで**切断してもジョブ継続**。再接続は job_id 購読、再オープンは履歴 API で復元（generating は snapshot + 継続）。
- **全モードサーバー側**（ユーザー指示）: chat/web/academic/deep を全て永続パスへ。web/academic/deep は `_server_search`（chat_router の検索/`_deep_search` を再利用）がジョブ内で検索→LLM。出典は ChatMessage.meta_json に保存し WS "sources" で配信。gen/run は元々サーバー計算（run は workflow executions で永続）。
- **UI**: AssistantChat の localStorage 履歴を廃止し DB 会話へ一本化。独立`/assistant` route、会話一覧・切替・新規・改名・削除、設定（モデル/検索）を実装。マウント時に DB 復元 + generating 再購読。
- chat本文は1秒ごとに`ChatMessage`へcheckpoint。genの生成/検証結果はサーバーjob resultと会話履歴から復元できる。実機で生成→schema/意味検証→登録→エディタ遷移を確認済み。

### D. ワークフロー生成の意味検証・品質スコア ✅ 完了
- **実装済み**: `validation.py` に `semantic_check`（到達不能ノード・存在しない変数参照・主要必須設定欠落・ループ/エージェント終了条件）と `quality_score`（構造/到達性/出力/エラー処理/実動作の 0-100 内訳）。
- `chat_router._validate_generated` が構造検証の後に意味エラーも返し、自動ビルドの LLM 修正へフィードバック。生成 API と build 完了イベントに `quality` を付与。UI（AssistantChat）に品質スコアバッジ（内訳・検証結果の折り畳み）を表示。
- **方針**: 完全な親子ジョブ分割ではなく、既存 `/chat/build`（既にジョブ化済み・generate→validate→register→run→自動修正）を強化する形（冗長化回避）。JSON Schema厳格出力を実装済み。
- executor、外部通信、process、DB更新、file write、secret復号を一切行わない静的dry-runを追加。保存済み/編集中definitionとnode単体の予定操作、副作用分類、capability、到達順、error/warningを返す。
- 36種のexecutor/control nodeすべてにversion、side_effect、capability、config/output型、retry/cancel/progress/dry-run対応をbackend metadataとして定義。LLM catalogとfrontend node集合の差を自動テストで禁止する。
- 詳細設計と不変条件は[`design-workflow-dry-run-metadata.md`](design-workflow-dry-run-metadata.md)へ統合。

### E. LLMランタイム抽象
- `LlmRuntimeProvider`（detect/install/list_models/start/stop/health/stream_chat/cancel/get_capabilities...）。
- `OllamaRuntimeProvider` + `LlamaCppRuntimeProvider`。既存 Ollama 実装を provider 化。

### F. llama.cpp 導入 ✅ 完了（2026-07-15再監査後に補完）
**F-1 完了（backend、実機動作確認済み）:**
- `models_mgmt/llama.py`: リリース asset 照合（vulkan/rocm/cuda・Linux）、DL+展開+SHA256（ジョブ `llama.install`）、`~/.local/share/control-deck/runtimes/llama.cpp/<tag>/<backend>/` + `current` シンボリックリンク。
- systemd ユニット `cdapp-llama.service`（cdapp- 前置で既存 systemd ヘルパー再利用）。**LD_LIBRARY_PATH=current** が必須（共有ライブラリ libllama-server-impl.so 等がバイナリ同階層）。
- start/stop/health、`detect_options`（`llama-server --help` 解析で実在フラグ 316 件・`--draft-*`=MTP/speculative も検出）。
- API: `/models/llama/{status,assets,install-jobs,config,start,stop,options}`。OpenAI 互換 `http://127.0.0.1:<port>/v1` として既存チャット/ワークフローから利用可（base_url 指定）。
- **実機検証**: ROCm 版を DL→展開→27B GGUF で起動→health OK→/v1/chat/completions 200→停止まで確認。experimental フラグ付き。
**F-2 完了（UI、環境検出/切替）:**
- Model 画面に「llama.cpp ランタイム」カード。`detect_backends` で **このマシンで使えるバックエンドのみ選択肢化**（ROCm=/dev/kfd+rocminfo、Vulkan=vulkaninfo/libvulkan）。CUDA は Ollama 案内で除外（ユーザー指示）。
- 複数GGUFはalias/port/pathを一意制約付きcatalogとして保存し、選択中aliasを従来の単一instance設定へmirrorする。
  既存設定は読み込み時に無損失移行し、1モデル=1個のhash付きsystemd user unitで独立起動する。
- モデルごとに自動起動、共通idle unloadからの除外、最終利用時刻、CTX/KV/MTP/MoE等を保存する。
  チャット、ワークフロー、RAGを含むLLM呼び出しでendpointに対応するinstanceの利用時刻を更新する。
- provider共通healthと、Ollama/llama.cppを合算する同時ロード上限を追加。詳細設計は
  [`design-llama-multi-instance.md`](design-llama-multi-instance.md)。
- 未導入=導入ボタン(DLジョブ)、導入済み=切替ボタン(`switch_backend` で current 張替・再DL不要)、使用中=✓。起動設定(モデルGGUF/GPU層数/ctx/flash-attn)+起動/停止。base_url を既存 LLM 設定に指定。
- モデル個別の型付き設定としてMTP（draft model/ngram）、K/V cache量子化、MoE CPU配置、context/output、batch/thread/sampling等を実装。実バイナリ`--help`にある能力だけ表示する。
- セキュリティ上、管理対象instanceのhostは127.0.0.1固定。外部providerは共通provider検出を利用する。

### G. OpenCode（オプトインのみ）
- **自動導入禁止**。`./deck.sh feature install/enable/disable/uninstall opencode`。
- feature registry（installed/enabled/available/version/health）。未導入時はルート/メニュー/ノード/コマンドパレット/API能力に**登録しない**（CSS非表示でなく未登録）。直URLは404。
- プラグイン境界: `backend/app/integrations/opencode/`, `frontend/src/features/opencode/`。汎用抽象 `CodeAgentProvider` 等にのみ依存。削除で他機能に影響しないこと。
- llama.cpp の OpenAI互換エンドポイントを provider に選択可。ワークフローは統合ノード `code.agent`（operation: analyze/implement/fix/test/...）を feature 有効時のみ登録。

### H. ノード超強化
- 既存ノードに version/capability/side_effect/supports_*(retry/cancel/progress/dry_run)/型付き入出力/help を付与。重複は operation 化。
- 便利ノード（parallel map/JSON transform/schema validate/csv/glob/health check/rerank/embedding/judge 等）は既存統合ノードと重複しない範囲で追加。
- ノード検索/カテゴリ/お気に入り/導入済み機能のみ表示。単体テストUI強化。

---

## 実装順序（推奨）
A（自己完結・実機あり）→ B（土台）→ C（Bに依存）→ D → E → F → G → H。
各フェーズ完了ごとにテスト通過・PR/マージ。**冗長化を避け既存の改修・統合を優先**。

## セキュリティ不変条件
shell=False/引数配列/許可ルート/symlink脱出防止/RBAC/CSRF/Origin/監査ログ/シークレット暗号化/root非要求/出力上限/プロセスツリー終了。read_only原則（PSUは読取のみ、sudo/hidraw書込禁止）。
