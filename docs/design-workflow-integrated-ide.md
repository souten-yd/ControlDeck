# ワークフロー統合開発環境 詳細実装仕様

最終更新: 2026-07-19  
状態: 実装仕様確定・Phase 1 UX 基盤コア完了
対象: 既存ワークフロー定義との後方互換を維持する段階的改修

## 1. 目的

ControlDeck のワークフローを、ノードを配置して実行する画面から、次の反復ループを一画面で完結できるローカル AI / 自動化統合開発環境へ発展させる。

> 入力を与える → 全体または 1 ノードを試す → 入出力を見る → 値を修正する → 任意地点から再実行する → 問題を特定する → 公開する

優先順位はノード数ではなく、プレビュー、観測、単体実行、再現、公開境界、型付き出力の統合とする。Dify / n8n の画面構成を模倣せず、ローカル LLM、RAG、Deep Research、OpenCode、PC 管理、センサー監視を同じ実行・監査・権限モデルへ統合する。

## 2. 用語と不変条件

- **実行前チェック（旧・安全プレビュー）**: executor、secret 復号、外部通信、プロセス、DB 更新、ファイル書込を一切行わず、構造・副作用・公開可否を返す静的解析。
- **draft テスト（旧・通常テスト）**: 同じ実行前チェックを先に表示したうえで、draft 定義とテスト入力を明示的に使う実実行。副作用は起こり得るが公開版は変更しない。
- **検証して実行**: 未保存変更を保存し、公開blockingがなければ現在draftと同じ版を必要時だけ公開し、そのimmutable version IDを固定して実行する複合操作。
- **公開実行**: published version の immutable snapshot だけを使う実行。
- **ノード単体実行**: 選択した入力 source を解決し、そのノードだけを実 executor または mock で実行する。
- **途中から再実行**: 指定ノードより前は保存済み node run または pinned data を使い、指定ノード以降を現在版または当時版の graph で実行する。
- **output contract**: UI、REST、scheduler、webhook、chat、subflow が共通で返す名前付き・型付き最終出力。
- secret 値は definition、snapshot、node run、event、log、artifact metadata、AI 診断 payload に保存しない。
- pinned data と mock data は draft/test 専用であり published version へ含めない。
- blocking error が 1 件でもあれば品質点に関係なく公開不可とする。
- Runner、schedule、Webhook、system event、外部 API はdraftを自動公開せず、明示済みの公開版だけを使う。
- NodeRunの実入力・実出力・error・log・artifact・tokenは保存前とAPI返却時にredactし、`total_tokens`等の数値metadataを認証tokenと誤認しない。

## 3. コード監査結果

README や実装状況の記載だけを根拠にせず、2026-07-19 時点の実コード、API、UI 導線、自動テストを確認した。

### 3.1 フロントエンド

| 機能 | 現在の実装 | 実装場所 | 問題 | 判断 |
|---|---|---|---|---|
| 一覧 | 一覧、即時実行、enable、JSON export、sample | `frontend/src/pages/Workflows.tsx` | 一覧の実行は入力定義を経由せず、draft/published を区別しない | 改修 |
| editor / canvas | 遅延 load React Flow、custom node、MiniMap | `WorkflowEditor.tsx` | 1,661 行へ責務集中。5 領域、undo/redo、search、layout がない | 分割改修 |
| custom node | status ring、approval/retry badge、分岐/error handle | `WorkflowEditor.tsx:FlowNode` | status 語彙、edge 状態、型、警告、reduced motion が不足 | 再利用・改修 |
| edge | 標準 edge、常時 animated | `WorkflowEditor.tsx:toFlow` | data flow/status を表さない | 専用 edge へ変更 |
| node palette | category、search、favorite、available、snippet | `WorkflowEditor.tsx:NodePalette` | backend schema を表示定義の正にできていない | 再利用・改修 |
| inspector | 1 Sheet に全設定、共通制御、safe node preview | `NodeConfigSheet` | 設定/入力/出力/実行/error/詳細 tab と実値がない | 全面改修 |
| config form | frontend `NODE_TYPES.fields` の型別分岐 | `nodeTypes.ts`, `ConfigInput` | backend `config_schema` と二重管理 | schema 駆動へ移行 |
| trigger input definition | text/paragraph/number/select/file | `TriggerInputsEditor` | default、description、validation、boolean/date/JSON 等がない | 拡張 |
| run input | 入力がある場合だけ別 `RunInputsSheet` | `WorkflowEditor.tsx` | preview/result、過去 input、test case と分断 | Preview へ統合 |
| safe dry-run | definition API + 別 `DryRunSheet` | `dryRun`, `DryRunSheet` | input/output contract/実結果への遷移が分離 | logic 再利用、UI 統合 |
| node test | `dry_run:true` のみ UI 提供 | `NodeTestRunner` | upstream/manual/cached/pinned input と実単体実行がない | API/UI 改修 |
| variable picker | trigger、upstream output、named vars | `VarPicker`, `upstreamDefs` | type/sample/null/source/previous/secrets/system、D&D が不足 | 全面改修 |
| live/history/version | polling の `InfoPanel` と別 `ExecutionsSheet` | `InfoPanel.tsx`, `WorkflowEditor.tsx` | 責務が二重化し input/output/log/cost/artifact が分散 | debug panel へ統合 |
| execution detail | context JSON の node 表示 | `ExecDetail`, `ExecutionsSheet` | resolved input/log/token/retry/artifact がない | node-run API へ移行 |
| editor chat | message で run し `signal.display` を polling | `ChatWindow` | preview と chat の役割が混在。typed output 非対応 | editor から廃止 |
| AI generation | assistant、WebSocket build、quality | `AssistantChat.tsx`, `chat_router.py` | preflight、patch diff、適用前確認、test case が弱い | 再利用・改修 |
| mobile | BottomSheet、省略 toolbar、MiniMap 非表示 | workflow components | 1 sheet mode 切替、320px command bar が未統合 | 改修 |
| stream | execution は 1.2–3 秒 polling、chat/build は WS | `InfoPanel.tsx` 等 | sequence 付き execution event がない | SSE 追加 |
| state | local state + TanStack Query | workflow components | editor history/debug の state 境界がない | 専用 store 追加 |
| tests | workflow 専用 frontend spec なし | `frontend/e2e/` | 過去の手動確認が継続回帰しない | E2E 追加 |

### 3.2 バックエンド

| 機能 | 現在の実装 | 実装場所 | 問題 | 判断 |
|---|---|---|---|---|
| Workflow | name、description、definition_json、enabled | `backend/app/models/__init__.py` | draft/published/archived、published version がない | 拡張 |
| WorkflowVersion | 保存前 snapshot、最大件数、restore | model + `router.py` | version/schema/checksum/published_at/creator がない | 拡張 |
| WorkflowExecution | status、trigger、時刻、error、context_json | model | version/snapshot/input/runtime/source が専用 field でない | 拡張 |
| node run 保存 | execution context 内 entry | `engine.py:run_single` | resolved input/log/token/artifact/attempt/cache source を query 不能 | model 追加 |
| structural validation | JSON、node/edge、trigger、type 等 | `engine.validate_definition` | type/output/secret/cost/branch/schema compatibility が浅い | pipeline 化 |
| semantic / quality | reachability、dangling ref、required、単純 score | `validation.py` | issue code/severity/path がなく存在確認中心 | structured issue へ拡張 |
| safe dry-run | 静的走査、redact、side effect/capability | `dry_run.py` | output contract、secret name、token estimate、typed input が不足 | 維持・拡張 |
| registry / metadata | executor、version、side effect、capability、簡易 schema | `nodes.py`, `node_metadata.py` | config schema が flat、UI/doc がなく三重管理 | metadata v2 |
| catalog consistency | executor/metadata/frontend/LLM の集合 test | workflow tests | frontend 定義内容は backend から生成されない | backend catalog を正にする |
| run API | 公開版実行、draft test、検証・必要時公開・固定版実行を分離 | `POST /workflows/{id}/run`, `/test`, `/validate-publish-run` | 外部入口は公開版限定。editorだけ複合操作を使用 | 実装済み、idempotencyは後続 |
| node test API | type/config を executor または preview | `POST /workflows/test-node` | workflow/node/upstream context がない | legacy + scoped API |
| execution API | list/detail/live/approval/cancel | `router.py`, `engine.py` | event replay、node run、retry/resume がない | 拡張 |
| scheduler | enabled workflow の mutable definition を起動 | `scheduler_loop` | published version を使わない | published のみに変更 |
| trigger | manual/schedule/webhook/alert event | engine/hooks/trigger config | system/file/email node 境界なし | adapter + 専用 node |
| signal.display | display/value/signal | `nodes.py`, `nodeTypes.ts` | renderer/name/schema がない | output.render alias |
| loop/flow.call | isolated parallel loop、subflow | engine/nodes | return contract、version pin、cycle graph が不足 | 改修 |
| secret | 実行開始時に全 secret を復号し context 注入 | `_load_secrets` | 必要 secret だけでなく全件。snapshot に name list なし | scoped 解決 |
| template | `{{node.field}}`, vars, secrets | `render_template` | type-preserving resolve、compile-time type check がない | resolver v2 |
| events | progress を context 更新し polling | engine/router | sequence/reconnect/event store がない | event stream 追加 |
| AI | schema 生成、semantic/quality、WS | `chat_router.py` | operation diff、diagnose、apply audit がない | patch service 追加 |
| migration | create_all + SQLite column-only light migration | `bootstrap.py` | 大規模 schema versioning に不足 | Phase 2 で Alembic |
| backend tests | dry-run、catalog、engine/loop/API | workflow tests | publish/replay/node-run/output/secret persistence が未試験 | 各 Phase 追加 |

### 3.3 評価

- backend 能力は高いが、実行時情報が可変 `context_json` に集中し、再実行の input source と immutable definition を再構成できない。
- editor は canvas、設定、入力、dry-run、chat、履歴、live result が別 UI で反復ループの移動コストが高い。
- `WorkflowVersion`、dry-run、metadata、parallel loop、approval/error handle は捨てずに土台として使う。
- `NODE_TYPES`、`NODE_CATALOG`、`node_metadata` は集合整合だけで内容が三重管理されている。metadata v2 に集約する。

## 4. 対象 UI

Desktop は `WorkflowCommandBar`、`NodeLibrary`、`WorkflowCanvas`、`NodeInspector`、`ExecutionDebugger` の 5 領域にする。command bar は戻る、名前、保存状態、draft/published、version、undo/redo、layout、fit、search、issues、preview、run、publish、more を持つ。

Mobile は戻る、短縮名、保存状態、preview、run、more だけを常設する。library、inspector、history、variables、issues、debugger は 1 つの bottom sheet 内で切り替える。固定 bottom tab bar は置かず、safe area、44px touch target、focus restore、320×700 の横 overflow 0 を満たす。

### 4.1 Mobile canvas interaction contract

- node tap は常に同じ高さ・同じ位置の inspector を開く。node typeやtabの内容量でsheetを移動・伸縮させず、内部だけscrollする。
- node削除は設定tab末尾へ隠さずinspector headerの44px actionへ置く。triggerなど削除不可nodeではaction自体を表示しない。
- handleは12pxの視覚サイズを維持しつつ、coarse pointerでは周囲16pxを含む44px相当の透明hit areaを持つ。nodeの`overflow`でhit areaをclipしない。
- edgeは32pxの透明hit pathで選択できる。選択時はaccentで強調し、固定toolbarに「端点をdragして付け替え」と削除を表示する。
- reconnect endpointのhit radiusは36pxとし、source/targetのどちらもdragで別handleへ付け替えられる。edge削除とreconnectはdefinitionをdirtyにする。
- canvas上の主要actionは選択対象の近くか固定位置にだけ出し、node cardを大型化したり全nodeへ常時buttonを並べたりしない。

## 5. Preview Workspace

editor 内 chat と `RunInputsSheet` / `DryRunSheet` を置換し、同じ surface で以下を扱う。

- Input: trigger schema から form 生成。test case、前回 input、過去 execution input の load/save/edit。
- Mode: safe preview（既定）、test run、run-to-node、resume-from-node、mock。
- Expected outputs: output contract の name/type/source/required/schema。
- Side effects: none/read/write/external/process、capability、LLM token estimate、secret **名のみ**。
- Result: final typed output、node result、duration、token、error/warning、artifact/citation、execution ID。

結果は execution/test case ID から復元できる。input と result を別 dialog へ分けない。

## 6. 定義 format v2

既存 `{nodes, edges}` はそのまま有効とし、省略時 default を補う。

```json
{
  "schema_version": 2,
  "nodes": [],
  "edges": [],
  "input_schema": {"type": "object", "properties": {}},
  "output_schema": {"type": "object", "properties": {}},
  "settings": {"timeout_seconds": 3600, "token_limit": null, "cost_limit": null, "concurrency": 5}
}
```

- v1 trigger `config.inputs` は read 時に v2 JSON Schema へ投影し、移行完了まで write 時も保持する。
- `signal.display` は loader で `output.render` 相当へ投影する。明示 migration まで元 type を保持できる。
- edge は `source_handle` / `target_handle` / `data_type` / `route` を追加し、旧 `branch` を alias とする。
- node は `node_version`、`disabled`、`group_id` を追加する。未知 field は round-trip で失わない。

## 7. Typed output contract

`output.render` は name/title/description/value/renderer/schema/required/empty_state/max_preview_size/downloadable/copyable/collapsible/sensitive/filename/mime_type を持つ。renderer は auto/plain_text/markdown/json_tree/json_raw/table/key_value/code/image/image_gallery/audio/video/file/link/status/metric/progress/citations。

複数 output node の `name` 重複は blocking error。UI、API、schedule、webhook、chat、subflow は同じ contract を返す。

```json
{
  "execution_id": 42,
  "status": "succeeded",
  "outputs": {"answer": {"type": "markdown", "value": "...", "mime_type": "text/markdown"}},
  "artifacts": [],
  "warnings": []
}
```

`sensitive:true` は UI 既定 mask、copy/download 禁止、DB には redact/参照のみとする。

## 8. Metadata v2 / schema-driven inspector

backend `node_catalog()` を UI/validator/AI generator/docs の正とする。metadata は type/version/title/description/config JSON Schema/input/output schema/handles/side effect/capabilities/supports/UI token/documentation を持つ。

JSON Schema property に `ui:widget/group/order/advanced/placeholder/secret` を許可する。frontend plugin component は trigger input editor、scrape selector、code editor、file/app/workflow picker など特殊 UI だけに限定する。

Inspector は全 node 共通で設定 / 入力 / 出力 / 実行 / error 処理 / 詳細 tab とする。入力 tab は名前、実値、型、source、nullable、updated time、出力 tab は schema と直近実値を表示する。

## 9. Data model

### Workflow / WorkflowVersion

- `Workflow`: `status(draft|published|archived)`、`published_version_id`、`draft_revision`、`autosaved_at`。
- `WorkflowVersion`: `version`、definition、input/output schema、created_by、published_at、checksum、node_versions。
- published version は immutable。restore は新 draft を作る。

### WorkflowExecution

- workflow_version_id、definition_snapshot、trigger_inputs
- allowlist 済み非 secret environment、runtime/model/sampling snapshot
- secret names、source、parent_execution_id、resume_from_node_id、output contract、last event sequence

execution 開始 transaction 内で snapshot を確定して enqueue する。secret 値は保存しない。

### WorkflowNodeRun

execution/node/attempt を一意とし、node type/version/status/resolved inputs/outputs/error/log ref/artifacts/token/時刻/elapsed/cache source/schema version を保存する。入出力は recursive redact と size 判定を通し、大容量値は artifact storage へ移す。

### TestCase / Pause / Pin / Event / Artifact

- `WorkflowTestCase`: name、inputs、mocks、expected outputs、assertions、last result。
- `WorkflowPause`: execution/node/type/form schema/token hash/status/expires/resumed。平文 token は初回のみ。
- `WorkflowPinnedData`: draft/node/output/schema/user。publish payload へ含めない。
- `WorkflowExecutionEvent`: execution/sequence/type/node/timestamp/redacted payload。TTL replay buffer。
- `WorkflowArtifact`: storage key、filename、mime、size、checksum、sensitive、owner。DB blob は使わない。

Phase 2 で Alembic を導入する。既存 SQLite は backup → migration → checksum/read test を行い、`create_all + light migration` は移行期間だけ両立する。

## 10. Execute / retry / resume semantics

1. definition/version と trigger input を snapshot。
2. template dependency と graph を compile し、type/capability/secret name を解決。
3. executor 呼出前に node `resolved_inputs` を保存。ただし secret 値を redact。
4. output を schema validation、redact、artifact offload 後に保存。
5. DB commit 後に event を単調 sequence で publish。

resume-from は対象 node の upstream cut を計算する。必要 output が選択 source にすべて存在し schema compatible な場合だけ実行する。不足時は blocking error とし、勝手に upstream executor を呼ばない。「選択 node まで実行」だけが upstream 実行を許可する。

- historical retry: execution snapshot の graph/node/model/sampling。
- current retry: 現 draft/published を選び、保存 output を compatibility check 後に使用。
- inputs-only: trigger input を Preview Workspace へ load するだけ。

## 11. Error route

対応 node は success/error/timeout/no_data handle を宣言する。error/timeout handle、typed Error Context、node timeout、
直近error inspector、変数picker、edge stylingは実装済み。engine は node_id/type/message/code/retryable/attempt/
redacted input summary/timestamp を持つ Error Context を routeへ渡す。Authorization、cookie、password、token、API key、
resolved secretはredactする。旧`on_error=continue|branch|stop`を維持し、timeout edgeのない既存flowはerror edgeへfallbackする。

## 12. API v2 / events

既存 API は削除せず deprecated header と adapter を段階導入する。

- `POST /workflows/preview-definition`, `POST /workflows/{id}/preview`, `POST /workflows/{id}/test`
- `POST /workflows/{id}/publish-check`, `POST /workflows/{id}/publish`, `POST /workflows/{id}/validate-publish-run`
- `POST /workflows/{id}/nodes/{node_id}/test`
- `POST /workflows/{id}/executions/{execution_id}/resume-from/{node_id}`
- `POST /workflows/{id}/executions/{execution_id}/retry`, `.../load-inputs`
- `GET /workflows/{id}/executions/{execution_id}/nodes`, `.../artifacts`
- test case CRUD/run/batch、publish/version detail/restore
- AI diagnose/patch/patch apply、pause submit
- `GET /workflow-executions/{id}/events?after_sequence=N`, `GET .../stream` (SSE)

全 endpoint は `workflows.run` / `workflows.edit` / 新設 `workflows.publish` を依存性で強制する。publish、retry、resume、pause submit、pin、AI patch apply は audit 対象。

event は execution_id、node_id|null、sequence、timestamp、type が必須。workflow.started/finished/failed/paused、node.queued/started/progress/log/output/retry/finished/failed、artifact.created を定義し、`Last-Event-ID` で再接続する。

## 13. Quality pipeline

issue は `{code,severity,message,node_id,path,details,autofix}`、severity は blocking/warning/suggestion。構造、到達性、型、required input、output contract、unused node/variable、branch coverage、loop limit、timeout、retry、error path、secret safety、side-effect approval、deterministic/runtime test、cost/token limit、large data、subflow cycle、optional capability、schema compatibility、pinned data を評価する。

score は issue と独立した説明値とし、publish 可否は `blocking.length === 0` と publish policy で判定する。

## 14. New nodes / ControlDeck differentiators

Phase 3 は output.render、human.approval、control.merge/try/delay、data.template/filter/aggregate、flow.return/error/note、trigger.webhook/file/system/email boundary を優先する。`output.render`、`human.approval`のruntime gate、`control.merge`の5方式、`data.template/filter/aggregate`、typed error/timeout routeは実装済み。approvalの永続pause／修正入力、try/delay、flow制御、system triggerを次の機能単位とする。

次段階は human.form、queue/cache/state/event、subworkflow map、batch/rate/circuit breaker、document/PDF/image/audio、unified notification、test.assert。

差別化機能は execution time travel、VRAM/CTX/model 状態を使う `ai.route`、GPU/VRAM/disk/llama-server/systemd/file event の `trigger.system`、AI failure diagnose/patch、test-case batch regression を優先する。sensor 取得不能は `N/A` / no_data としアプリ全体を落とさない。

## 15. Large flow / performance / accessibility

- node/edge を memo、selector 単位 store、node ID patch にする。
- output 全量を React state に置かず query cache + virtual log + lazy JSON tree。
- 100 nodes は通常編集、500 nodes は最低限 read-only navigation を acceptance とする。
- autosave 750–1500ms debounce、optimistic revision + conflict UI。
- event は 50–100ms batch。layout は worker adapter に隔離。
- group/collapse/outline/search/fit selection/layout/quick add/typed connection/undo/redo を Phase 4 で追加。
- 全 button aria-label、keyboard/focus restore、色以外の状態表現、reduced motion、44px touch、320px overflow 0。

## 16. AI generation / diagnose / patch

- 生成前に目的、trigger、input/output、side effect、使用 node、未導入 capability、risk を plan 表示。
- 生成後に layout、schemas、issues、quality、execution order、side effects、unknowns、test cases を表示。
- patch は versioned operation list を canonical とし JSON Patch export も可能にする。
- diagnose payload は redact 済み workflow/node run/log/runtime の最小集合。cause/confidence/options/impact/operations を返す。
- apply 前に canvas/raw diff を表示し、明示操作後に audit を記録する。

## 17. Samples / node documentation

全機能実装後に最低 15 sample を提供する。指定 10 E2E flow に、time travel、local LLM route、PC state recovery、AI patch、regression batch の 5 flow を加える。

各 sample は goal、difficulty、estimated time、required capabilities、side effects、required secrets/models/apps、typed input/output、sample input、expected assertions、mock data、node walkthrough、failure injection、recovery/retry、install 前 preview を持つ。

各 node doc は purpose、when/when-not-to-use、全 config、typed inputs/outputs、変数例、副作用、権限、secret、retry/timeout/error route、代表 error、performance/cost、2 以上の recipe、migration note を持つ。backend metadata を正とし SampleBook、inspector help、AI catalog で共用する。

### 17.1 Guided configuration（2026-07-19実装）

`node_catalog()`のmetadata v3を正として、`initial_config`、config fieldごとの`default`/`recommended`/`reason`、`primary_input`/`primary_output`、`quick_start`、`examples`を返す。新規ノードは`initial_config`を複製して開始し、「推奨値を適用」は未入力fieldだけへ反映する。外部URL、path、model、Secret、管理対象IDのような環境依存値は自動生成しない。

edge接続時はtargetの`primary_input`が空の場合だけ、sourceの`primary_output`またはtriggerの先頭typed inputを参照式として設定する。変数pickerは逆向きBFSで到達可能な上流だけを扱い、直前ノードとその他上流を分離し、backend output schemaの型、dynamic trigger/scrape/error output、直近実行サンプルを検索表示する。挿入は文字列末尾固定ではなく、現在のselection rangeへ行う。

Inspectorの設定面には必須表示、推奨値、理由、最短手順を置き、詳細面では構成例を確認・反映できる。既存definition、ユーザー入力値、Secret参照は自動migrationや推奨適用で上書きしない。

## 18. Phase / PR plan

- **PR 0**: 本監査・仕様、implementation plan/status、README 導線。
- **Phase 1 UX**: Preview、typed trigger form、expected/final output、inspector tabs、debug panel、canvas status、history input load。
- **Phase 2 reproducibility**: Alembic、version/publish、node run、snapshot、test case、pin、event、node test、retry/resume/time travel。
- **Phase 3 node/error**: output.render、approval/merge/try/data/system trigger、typed/error handles。
- **Phase 4 large flow**: group/collapse/subflow/outline/layout/performance。
- **Phase 5 AI**: diagnose、operation patch、auto test、runtime route、Project Intelligence。
- **Phase 6 samples/docs**: 15+ sample、全 node 詳細 doc、10 指定 E2E + 5 差別化 flow。

各 Phase は独立 branch/PR。backend test、frontend build、該当 Playwright、実 service/API、1280×800/768×1024/390×844/320×700 を確認してから merge する。

## 19. Verification strategy

### Deterministic automated layer

executor 非呼出 preview、secret 非永続、catalog consistency、schema/output、branch/merge/loop/parallel/retry/timeout/cancel/pause/resume、historical replay/version snapshot、optional unavailable、artifact limit、subflow cycle、assertion を backend で固定する。

frontend は trigger-to-result、preview/run distinction、inspector tabs、variable mismatch、pin、timeline、old/current retry、renderer、mobile sheet、undo/redo/autosave、draft/published、AI diff を component/E2E で固定する。

### Actual runtime / LLM evaluation layer

mock 成功だけで LLM/RAG/AI 機能を完了扱いにしない。利用可能なローカル Ollama/llama.cpp endpoint と実 model を必要に応じて使い、生成品質、structured output、token、latency、cancel、timeout、fallback、VRAM route、model unavailable、RAG citation、Deep Research、AI diagnose/patch を評価する。

各機能の検証記録は model ID、endpoint 種別、sampling、input/output schema、elapsed、token、結果、fallback 有無を残すが prompt 内 secret や機微 input は残さない。model 非依存機能は LLM を無理に介在させず deterministic test と実 service 操作で評価する。

実 service では認証 API と health を確認後、test 専用 workflow/user を使う。外部送信・process・file write は safe preview と隔離した test target / 一時許可 root だけで確認し、検証後 cleanup する。

## 20. Completion / compatibility risks

release blocker は、同一 Preview surface の input/result、node 実 input/output、過去 input、failed-node resume、historical/current retry、typed renderer、draft/published、live node/edge status、secret 非漏えい、320px 2-step/overflow 0、full backend/build/Playwright/migration test/実 service。

- `signal.display` は即時変更せず alias adapter。
- legacy run API は警告期間まで現挙動を維持し、scheduler/webhook の published 強制は migration wizard 後。
- context JSON read adapter を残し、過去 execution を閲覧可能にする。
- optional `code.agent` の feature registry 境界を維持する。
- SQLite migration 前に backup/disk check、失敗時は明示 error。
- definition unknown fields を落とさず、v1/v2 round-trip fixture を強制する。
