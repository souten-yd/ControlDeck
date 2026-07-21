# ControlDeck Application Builder 詳細実装仕様

最終更新: 2026-07-21
状態: Phase A、F1〜F3.7、B1〜B3 C# Console／隔離build、C1〜C2 ASP.NET API／schema／background job、D1〜D2 Entity、E1〜E7 Blazor GUI／CRUD／browser session／Workflow form／typed client state／Entity・API query／filter／sort／pagination／Secret／bounded side-effect source、Workflow契約ベース自動アプリ化を実装・検証完了

## 1. 目的と不変条件

既存Workflowから、ControlDeckに依存せず実行・配布できるアプリケーションを決定的に生成する。主要操作はWorkflowを選ぶことであり、既知の入出力契約から動作可能なGUIを自動構成する。Workflow、データモデル、GUI、API、永続化、background jobを構造化仕様として編集できるが、利用者へゼロからの画面設計を要求しない。LLMは必要な場合の仕様再提案とJSON Patchだけを行う。

> Workflowを選ぶ → 入出力から動作保証baselineを自動構成する → そのまま生成する、またはAI再検討／Canvas修正を行う → 差分を検証・選択適用する → 対象platform向けに生成・動作確認する

```text
Workflow Definition
  → Workflow IR
  → Application Spec
  → Application IR
  → structured diagnostics / security / target validation
  → deterministic target generator
  → generated project
  → isolated build / test / package
```

- LLMにC#、C++、HTML、XAML等の自由なsource codeを直接生成させない。
- LLM出力はschema constrainedなApplication Spec proposalまたはJSON Patchに限定する。
- generatorは同じSpec、Workflow snapshot、generator versionから同じbyte列を生成する。
- Workflow IR（処理）とApplication IR（画面・API・DB・配布）を分離し、1 Workflowを複数Application IRから共有する。
- 既存 `{nodes, edges}` definitionを変更せず、Application Projectを独立保存する。
- backend metadata / schema / registryを唯一の正とし、frontendへtarget対応表やcomponent schemaを直書きしない。
- 未実装機能をhidden buttonや成功するdummy buildで表現しない。`design/source/build/package/signing`を別状態で返す。
- Secret値をdefinition、Spec、IR、source、manifest、log、snapshot、LLM request/responseへ保存しない。

## 2. 現行コード監査

| 対象 | 実装済み | 場所 | 再利用 | 不足 |
|---|---|---|---|---|
| DAG semantics | parallel、first/all input、loop、retry、timeout、cancel、error route、approval | `workflows/engine.py` | Workflow compilerの意味論 | portable IR、join policy明示 |
| executor | HTTP、file、DB、C++ build、LLM、RAG等45標準node | `workflows/nodes.py` | runtime/native/remote分類 | target generator |
| metadata | side effect、capability、flat config/output schema | `node_metadata.py` | metadata v3へ互換拡張 | input/UI/security/codegen |
| catalog | LLM向け説明/config key | `catalog.py` | compatibility adapter | metadataとの二重管理 |
| validation | structural/semantic/quality | `engine.py`, `validation.py` | compiler preflight | structured Diagnostic、type/target |
| dry-run | executor/secret/I/Oなしの計画 | `dry_run.py` | Static Preview安全境界 | Application UI/binding preview |
| version/contract | immutable公開版、trigger schema、typed output | workflow model/router/contracts | IR ports、Runner、form/result | portable type system |
| job/process | browser非依存job、OpenCodeの`systemd-run --user` | `jobs`, `integrations/opencode` | Build orchestration/isolation | build phases/artifacts/SDK allowlist |
| Project preview | inline test、Managed App web proxy | `applications/testrun.py`, `webview.py` | Project Labの一部 | persistent run、run-scoped proxy |
| editor | command bar、inspector、preview、debug | `WorkflowEditor.tsx` | `[アプリ化]`入口だけ | 1,896行へ責務追加禁止 |
| DB migration | create_all + SQLite ADD COLUMN | `bootstrap.py` | Phase A additive model | Phase B前のAlembic計画 |
| .NET SDK | 現ホストでは未検出 | host audit | Phase Aはbuild不要 | Phase Bで明示diagnostic |

既存engineを捨てず、definitionを共通IRへcompileする。既存routerや巨大WorkflowEditorへ実装を集中させず、`application_builder/` とfrontend featureへ分離する。

## 3. データ層とApplication Spec v1

Phase Aの`ApplicationProject`: id/name/description/workflow_id nullable/application_spec_json/schema_version/target/application_type/ui_framework/status/created_by/created_at/updated_at。

Phase AではApplicationVersion、Build、Artifact、DesignConversationを作ったふりをしない。後続migrationでimmutable version、build、artifact、redacted design conversation、atomic operation historyを追加する。

```json
{
  "schemaVersion": 1,
  "application": {
    "name": "ServerMonitor",
    "displayName": "Server Monitor",
    "description": "PC状態を監視する",
    "applicationType": "web",
    "authentication": "local",
    "database": "sqlite"
  },
  "theme": {"preset": "control-deck-modern", "mode": "system", "tokens": {}},
  "navigation": {"type": "sidebar", "items": []},
  "pages": [],
  "entities": [],
  "apiEndpoints": [],
  "backgroundJobs": [],
  "workflows": [],
  "permissions": [],
  "targets": [{"id": "web", "platforms": ["web"], "framework": "aspnet-blazor"}]
}
```

未知fieldはround-tripで失わず、schema migrationを明示する。Phase A validatorはidentifier、参照、重複ID、enum、target、Workflow binding、secret literalを検査する。

## 4. Workflow IR / Application IR / 型

Pydantic v2 modelとして `WorkflowIR`, `NodeIR`, `EdgeIR`, `PortIR`, `ExecutionPolicyIR`, `NodeCodegenIR`, `SecretReferenceIR`, `Diagnostic` を追加する。

- Workflow: schema/version IDs/name/input/output/nodes/edges/required secret names/capabilities/side effects/diagnostics
- Node: stable ID/type/version/display name/redacted config/input/output/execution/codegen
- Execution: async/retry/backoff/timeout/on_error/join/approval/cancel
- Edge: ID/source+port/target+port/branch/type/optional Expression IR

Compilerは現在のengine semanticsを再解釈せず、trigger、branch、loop body/done、error/timeout/no_data、merge、retry、timeoutを明示IRへ投影する。許可されたloop以外のcycleはblocking。

共通 `TypeRef` は `any|null|boolean|integer|number|decimal|string|date|datetime|duration|bytes|file|directory|url|json|object|array<T>|map<K,V>|optional<T>|stream<T>|table<Row>|image|audio|video`。target mappingを分離し、`any` fallbackは `TYPE_UNRESOLVED` warningを残す。

Application IRはpage/layout/semantic component/binding/event/navigation/theme/responsive/entity/API/background job/auth/deployを正規化する。Binding sourceは `workflow-input:`, `workflow-output:`, `node-output:`, `api:`, `entity:`, `query:`, `state:`, `route:`, `form:`, `system:`, `constant:`。

## 5. Metadata v3 / diagnostics / capability

既存metadata responseへ後方互換でinput schema、codegen、UI hints、securityを追加する。codegen supportは`native|runtime|external|manual|unsupported`。Node、semantic component、framework、host build、packagingのregistryをbackendで管理する。

```json
{
  "code": "TYPE_UNRESOLVED",
  "severity": "warning",
  "message": "出力型を確定できません",
  "path": "workflow.nodes.http1.outputs.body",
  "source": "workflow-compiler",
  "suggestedFix": "output JSON Schemaを設定してください",
  "autoFix": false,
  "details": {}
}
```

カテゴリはWorkflow/Type/GUI/Binding/Database/API/Security/Target/Build/Accessibility。blockingをscoreで相殺しない。

## 6. Phase A APIと画面

- `GET /application-builder/schema`: Application Spec JSON Schemaとcomponent/target enum
- `GET /application-builder/capabilities`: node/framework/host capability
- `POST /application-builder/validate`: Spec単体またはWorkflow ID付きcompile + validation。I/O、LLM、buildなし
- Application Project CRUD
- `POST /workflows/{id}/application-projects`: 公開/下書き選択を明示して基本Specを作成

権限は専用view/editを追加し、作成・変更・削除をauditする。validateはnormalized Spec、Workflow IR、Application IR、diagnostics、capabilityだけを返し、build ID/artifactは返さない。

FrontendはWorkflow command barのその他へ`[アプリ化]`、project一覧、基本project画面（概要、Workflow、Spec summary、target capability、Diagnostics）を追加する。build/generate/publish/AI buttonはPhase Aに置かず、schema/capabilityはbackendから取得する。

## 7. GUI Editor / Design System（後続）

Desktopは左Library/Tree、中央Preview、右Inspector、下Data/Event/Diagnostics/AI。配置はStack/Row/Column/Responsive Grid/Card Grid/Split/Tabs/Drawer/Sidebar/Toolbar/Modal/Bottom Sheet/Master-Detail。通常の絶対座標は禁止する。

追加、親変更、並べ替え、複製、削除、group、別page移動、responsive限定移動、keyboard reorderを持つ。Component Treeは展開、検索、filter、hidden/lock/binding/diagnostic、multi-select、keyboard移動を扱う。

Specには`data.table`, `chart.line`, `input.text`, `action.workflow-run`等のsemantic componentを保存し、framework classは保存しない。

color/surface/text/accent/status/spacing/radius/shadow/type/control height/motion/breakpoint/z-indexをtoken化する。PresetはModern/Compact/Touch/Dashboard/Data Dense/Minimal/Terminal/Media。Primitive、KPI/job/log/CRUD/timeline等のcomposite、Dashboard/Settings/Wizard/Launcher等のpatternをbackend registryで管理する。

## 8. AI Designer（後続）

scopeはapp/navigation/page/section/form/chart/table/breakpoint/style/binding。Preserve/Balanced/Redesign modeとstructure/binding/style/position/content lockを入力する。

原則3案（Simple/Balanced/Dense）を返し、Desktop/Mobile、操作数、部品数、密度、a11y、mobile適合、生成難易度、target対応、理由、注意を比較する。responseはsummary/rationale/affected IDs/JSON patches/preview notes/warnings/validation expectationだけ。

patchはlock違反、path escape、unknown component、binding/type/securityをvalidateし、適用前にstructure diffとvisual previewを出す。partial applyでき、1 proposal applyを1 atomic undo operationにする。

LLMへ渡すのはredacted schema、component tree、binding名、sample、diagnostic、要求。secret、cookie、authorization、`.env`、SSH/private key、任意file、DB全件は渡さない。

## 9. Platform Advisor / target profiles

1 SpecはDomain、Workflow IR、Entity/API、validation、permission、design token、component semanticsを共有し、window/navigation/file picker/tray/notification/background/updater/mobile lifecycle/permission/packageだけtarget profileへ分ける。

Advisorは対象OS、offline、local file、tray、background、GPU、embedded server、store、優先言語/size/native feel/web reuse等をscoreし、上位候補と理由・制約・代替を返す。overrideと複数targetを許可する。

2026-07-19の公式情報に基づく初期registry判断:

- Web/PWA / ASP.NET: 初期正式target候補。
- Avalonia: Windows/macOS/Linux/iOS/Android/WebAssembly。ただしplatform tierとmobile workloadを別診断する。<https://docs.avaloniaui.net/docs/supported-platforms>
- Tauri 2: desktopとAndroid/iOS CLI。plugin対応、host toolchain、package差を個別診断する。<https://v2.tauri.app/distribute/>
- Electron: Windows/macOS/Linux desktop。Android/Webの直接generatorにしない。<https://www.electronjs.org/docs/latest/>
- .NET MAUI: Android/iOS/macOS/Windows。Linux必須時の第一候補にせず、Apple buildのMac要件を返す。<https://learn.microsoft.com/en-us/dotnet/maui/supported-platforms>
- Flutter: mobile/web/desktop。初期はadvisor/designのみ。<https://docs.flutter.dev/reference/supported-platforms>
- Compose Multiplatform: Android/iOS/desktop/web。platform成熟度をregistry versionへ固定する。<https://kotlinlang.org/compose-multiplatform/>
- C++/Qt、Spring/Ktorは後続。

Capabilityは `spec/source/localBuild/remoteBuild/package/signing/store/stability` を分ける。Ubuntu hostからiOS/macOS署名を成功扱いしない。正式生成優先は Web/PWA → Avalonia Windows/Linux → Tauri Windows/Linux。未実装targetをstable表示しない。

## 10. Generator / build / Project Lab（Phase B以降）

生成物をManaged (`*.generated.cs`, `Generated/`, manifest)、Extension (`Extensions/`, partial/interface)、Configへ分離する。manifestはgenerator/spec/workflow checksumとmanaged file checksumを持ち、手編集を検出して無言上書きしない。

大きな単一string templateを避け、identifier sanitation、namespace、escaping、deterministic sort、LF、locale independent numberをtestする。Generation modeはNative/Embedded Runtime/Remote ControlDeck/Hybridで、Phase B標準はHybrid。node判断はmetadata/registryだけに置く。

Build Jobはqueued/preparing/generating/restoring/building/testing/packaging/completed/failed/cancelled。systemd user transient unit、argv配列、SDK allowlist、resolved root、symlink containment、timeout/cancel/resource/log/concurrency/redactionを強制する。

Project Labは生成物と`~/CodeDEV`の実行・評価面を兼ねる。CLI、Web run proxy、static HTML sandbox、image/chart、CSV/JSON、PDF/media、artifact、errorを表示する。native GUIは直接embedせずremote desktopへ誘導する。

## 11. Phase境界

### Phase A — 最初のApplication Builder PR（これ以外を混ぜない）

1. Application Spec v1
2. ApplicationProject model + additive migration
3. Workflow IR / Application IR compiler
4. type system / structured diagnostics
5. generator/target capability registry（生成なし）
6. schema/capabilities/validate/project CRUD API
7. `[アプリ化]`入口とApplication Project基本画面
8. unit test、frontend build/E2E、設計/status更新

禁止: dummy generate/build、見せかけartifact、自由code LLM、Phase B generator、GUI drag editor。

### Phase B〜G

- B: C# Console native優先node、deterministic generator、tests/source zip。SDKなしは明示diagnostic。
- C: ASP.NET API/OpenAPI/async job/SSE/health/Docker。
- D/E: GUI、trigger form/output/table/chart/responsive、Entity/SQLite/PostgreSQL/migration/CRUD。
- F1: semantic component tree/D&D/undo。
- F2: structured multi-proposal/partial patch/locks/evaluation。
- F3: tokens/composites/patterns/template/a11y。
- G1: advisor/framework/build matrix/comparison。
- G2: Web、Avalonia、Tauriの順にstable generator。

### Phase F1.1 — Semantic Component foundation（2026-07-19実装）

F1はbackend catalog／検証とfrontend editorを分割する。最初の単位では`layout.stack/row/grid/card`、`display.text/markdown/metric`、`input.text`、`action.workflow-run`、`data.table`、`chart.line`をframework非依存typeとして登録する。各定義はcategory、container可否、決定的defaultを持ち、`GET /application-builder/schema`から配信する。

Pageは任意のframework classではなく再帰的な`root` semantic component treeを持つ。component IDの全Page横断一意性、unknown type、primitiveへのchildren、binding prefix、lock schemaを保存前とvalidate APIで検証する。既存のrootを持たないPage辞書は後方互換で読み書きできる。次のF1.2 UIはcatalogを取得してpalette/tree/inspector/previewを構築し、component一覧をfrontendへ直書きしない。

### Phase F1.2 — Component Editor（2026-07-19実装）

Application Editorはschema APIのcatalogからpaletteを生成し、Page rootを起点とするTree、Preview選択、Binding／Properties Inspectorを同じ編集stateへ接続する。追加先は選択中container、primitive選択時はPage rootとする。Desktopのdragはcontainerへのreparent、touch／keyboardは明示的なMove操作を使い、dragだけに依存しない。

編集operationは最大50 snapshotのUndo／Redo履歴を持ち、Saveまではlocal draft、Save時は既存PATCH APIのbackend validationを必ず通す。Previewはsemantic componentを直接React classへ保存せず、320／768／desktop frameで決定的に描画する。現段階はstatic previewであり、Workflow実行、network、DB、secret解決を行わない。

### Phase F2.1 — Structured Patch foundation（2026-07-19実装）

AI提案と手動差分の共通境界としてRFC 6902の`add/remove/replace/move` subsetを採用する。1 requestは最大200 operation、JSON Pointerは最大64階層とし、不正escape、範囲外array index、prototype pollution token、Application Spec外rootを拒否する。`copy/test`は現段階では未対応としてschema段階で拒否する。

`POST /application-builder/patches/preview`はSpecをdeep copyしてPatchを評価し、base/result SHA-256、patched Spec、適用operation、structured diagnosticを返す。executor、LLM、network、DB write、secret解決は行わない。完成Specへ既存schema／component／binding／secret／target validationを再適用する。

`POST /application-projects/{id}/patches/apply`はPreviewが返したbase checksumを必須とし、保存済みSpecとの差異を409 `PATCH_BASE_CHANGED`で拒否する。全operationと完成Specが有効な場合だけ1 transactionで保存し、patch件数とchecksumを監査する。structure/binding/style/position/content lockは対象componentとancestorの双方で検証し、AIが先にlock自体を解除するPatchも禁止する。F2.2 frontendはこのPreview結果だけを差分・部分選択UIへ表示する。

### Phase F2.2 — Patch Review／部分適用（2026-07-19実装）

Application Editorの`Review Patch`は、保存済みApplication Specをbaseとして1〜200件の構造化Patchを読み込む高度編集入口である。frontendは`add/remove/replace/move`以外を候補として受け付けず、各operationをcheckboxで部分選択できる。依存関係のあるoperationをfrontendだけで推測せず、選択された正確なsubsetをPreview APIへ送り直す。

PreviewはBefore／AfterのPage数・Component数、structured diagnostic、base/result checksumを表示する。選択内容を変更した時点で以前のPreviewを破棄し、同一subsetの再Previewが成功するまでApplyを無効にする。ApplyはPreview時のbase checksumと選択subsetを送るため、並行更新と画面上の見かけだけの成功を防ぐ。

Component Inspectorからstructure／binding／style／position／content lockを設定できる。lockはApplication Specへ保存され、Patch Reviewでもbackendが強制する。`PATCH_LOCK_VIOLATION`はApply前に表示し、変更操作を無効化する。現段階のBefore／Afterは構造summaryであり、視覚diff・3案比較・LLM提案生成はF2.3以降として未実装状態を明示する。

### Phase F2.3 — Structured AI Design Proposals（2026-07-19実装）

`POST /application-projects/{id}/design-proposals`は、登録済みLLM endpoint/model、3〜4000文字の要求、application/page/component/mobile scope、Preserve/Balanced/Redesign modeを受け取る。任意URLを直接呼ばず、既存provider catalogに登録されたendpoint/modelだけを許可する。共通runtime providerを通すため、管理中llama.cppは停止中でも起動・model load完了を待ち、Ollamaはnative structured outputを利用する。

LLM contextはApplication Specから秘密らしいkey/valueとsecret templateをredactし、各文字列2000文字、全体60000文字へ制限する。加えてbackend catalogのSemantic Component、Design Token、Binding Sourceだけを許可部品として渡す。source code、Secret値、DB実データ、任意fileは送らない。

LLM応答はSimple／Balanced／Denseの3案、説明、理由、警告、RFC 6902 subsetに限定する。任意JSON値はgrammar差を避けて`valueJson`文字列として受け、backendで再parseして正式なApplicationPatchOperationへ変換する。OpenAI互換runtimeが単一要素arrayを文字列化した場合は理由／警告の文字列だけを決定的に正規化し、未知field・code・不正Patchを自由に受理しない。各案はF2.1 Previewで独立検証し、自動適用しない。

frontendは要望、scope、mode、検出modelを選び、3案をカード比較する。案を選ぶとF2.2 Patch Reviewへ移動し、operation部分選択、checksum、lock、Applyを再確認する。現段階は構造案比較であり、Desktop/Mobileの視覚差分合成、案の部分合成、AI自動修復は後続F2.xとする。

Application Spec v1の`llmRuntime`は`none/external/embedded/remote`を区別する。現在正式に編集できるのは`none`と`external`で、External providerはOllama／LM Studio／OpenAI互換を選び、`bundleRuntime=false`を必須とする。接続は`LLM_BASE_URL`／`LLM_MODEL`で注入し、runtime binaryやSecretを生成物へ埋め込まない。Embedded／Remoteはgenerator未実装のためUIでplannedかつ選択不可とする。

### Phase F3.1 — Design Token／Composite／Pattern foundation（2026-07-19実装）

backend Design System catalogをschema version 2へ更新し、color／surface／text／accent／status／spacing／radius／shadow／typography／density／control height／motion／breakpoint／z-indexの14 token群を唯一の許可値として配信する。Application Specの`theme.preset`と`theme.tokens`は保存・Patch適用時に同じregistryで検証し、未知preset、未知token、未登録値、任意CSSをblocking diagnosticとして拒否する。

初期presetはModern／Compact／Touch／Dashboard／Data Dense／Minimal／Terminal／Mediaの8種。CompositeはKPI Card／Job Status／Log Viewer／CRUD Table／Timelineの5種、PatternはDashboard／Settings／Wizard／Launcherの4種を、既存Semantic Componentだけからなる決定的treeとして配信する。frontendは名称やtreeを二重定義せず、catalogから選択して現在のcontainerまたはPage rootへ挿入し、既存tree全体と衝突しないIDへ再採番する。全操作は既存50段Undo／Redoと明示Saveへ統合する。

Previewは保存されたsemantic tokenをCSS変数へ投影し、accent、spacing、radius、surface、text、typography、shadowをDesktop／Tablet／Mobileで共通描画する。input/actionの明示的な空labelは`A11Y_LABEL_REQUIRED`で拒否する一方、省略時はcatalog defaultを使い既存Spec互換を維持する。property editorと全状態previewはF3.2で実装し、視覚diff、template parameter、generatorへのtoken mappingは後続とする。

### Phase F3.2 — Schema-driven Properties／全状態Preview／a11y（2026-07-19実装）

Design System catalogをschema version 3へ更新し、11 Semantic Componentすべてへproperty schemaを追加する。fieldはstring／multiline／boolean／number／enum／JSON、required、enum option、数値範囲を表現し、frontend Inspectorはこのmetadataから通常入力を描画する。複雑なcolumns／series／responsive columnsと将来fieldのため、既存のProperties JSONもadvanced編集面として維持する。

保存、Patch Preview、AI proposal Previewは同じbackend validatorを通り、property型、enum、数値範囲を`COMPONENT_PROPERTY_*` diagnosticで拒否する。input/action/table/chartの明示的な空labelは`A11Y_LABEL_REQUIRED`、rootを持つPageの空titleはwarningとして返す。省略値はcatalog defaultを解決するため既存Specを破壊しない。

Preview stateもbackend catalogを正とし、Default／Loading／Empty／Error／Disabledを同じApplication Specとviewportで切り替える。Loading skeleton、empty table/chart/metric、user-facing error、disabled interactionを副作用なしで描画し、`aria-busy`、status／alert、chart role/labelを付与する。状態は設計確認用でApplication Specへ保存せず、fake runtime executionを行わない。Grid／Table／Chartの高度editorはF3.3、binding/event固有editorはF3.4で実装し、残件はfocus/keyboard/contrastの自動検査、視覚diff、template parameterである。

### Phase F3.3 — Structured Grid／Table／Chart Editor（2026-07-19実装）

汎用JSONへ残っていたResponsive Grid列数、Table columns、Chart seriesをproperty schemaの専用型へ昇格する。Gridはmobile／tablet／desktopを各1〜12列、Tableは最大50列のkey／label／type、Chartは最大20 seriesのkey／label／semantic toneとしてbackend catalogから配信する。

validatorはbreakpoint、数値範囲、最大件数、英字始まりのkey、重複key、空label、未登録column type／series toneを保存・Patch・AI proposalで同じ`COMPONENT_PROPERTY_*`／`A11Y_LABEL_REQUIRED` diagnosticへ変換する。自由なformatter code、CSS色、式は受け付けない。

frontendは3 breakpointの数値control、Table column／Chart seriesの追加・削除・key・label・type/tone editorを44px touch targetで描画する。値は既存Undo／Redo、dirty、明示Saveへ統合する。Previewは選択中viewportのGrid列数をcontainerへ反映し、Table headerとChart series labelを表示する。columns／seriesはstatic design sampleであり、DB、Workflow、networkを実行しない。次はbinding/event editor、template parameter、視覚diff、focus/contrast auditである。

### Phase F3.4 — Structured Binding／Event Editor（2026-07-19実装）

Design System catalogをschema version 4へ更新し、Binding sourceを安定ID、表示label、reference labelの定義として配信する。Componentごとのevent schemaはevent名、表示label、許可actionを持ち、共通action schemaはRun workflow／Navigate／Set stateのtarget labelと参照先sectionを定義する。frontendはこのmetadataを二重定義しない。旧`bindingSources`配列と`source:reference`文字列は後方互換のため維持する。

validatorはBinding source、1〜512文字のreference、Secret template禁止を検査する。Eventは対象Componentに存在するeventと許可actionだけを受理し、1〜256文字のtarget、Workflow／Page IDの存在、英字始まりのState key、Secret template禁止を検査する。保存、Patch Preview、AI proposal Previewは同じ`BINDING_*`／`COMPONENT_EVENT_*` diagnosticを通る。任意JavaScript、handler式、runtime objectは保存しない。

InspectorはBinding source／referenceとEvent enable／action／targetを44px以上のcontrolで描画し、既存Undo／Redo、dirty、明示Saveへ統合する。Binding objectも読み取るが、UI保存値は互換性のある`source:reference`へ正規化する。Event初期targetはactionごとに決定的な値を使う。Previewは構造だけを表示し、Workflow実行、navigation、network、DB、Secret解決を行わない。視覚diffはF3.5、template parameterとfocus／keyboard／contrast auditは後続とする。

### Phase F3.5 — Visual Preview Diff／3案比較（2026-07-19実装）

通常EditorのSpec Previewを共通の読み取り専用rendererへ分離し、Patch ReviewとAI Design Proposalも同じrendererを使用する。Page root、Semantic Component、Design Token、Default／Loading／Empty／Error／Disabled、Mobile／Tablet／Desktopの解釈を比較面へ二重定義しない。rendererは選択callbackがある通常Editorだけcomponent選択を行い、比較面ではWorkflow、Event、navigation、network、DB、LLM、Secret解決を実行しない。

Patch Reviewはbackendが選択Patchをdeep copyへ適用し、全validatorを通した`patchedSpec`だけをAfterとして描画する。保存中SpecをBeforeとし、同じviewportをMobile／Tablet／Desktopで同期切替する。operation選択が変わったら旧checksum、diagnostic、視覚比較を破棄し、再PreviewまでApplyできない既存境界を維持する。無効案もdiagnosticと見た目を確認できるがApplyは不可とする。

AI DesignはSimple／Balanced／Denseの3案それぞれの`preview.patchedSpec`を同一viewportで並べ、説明、理由、Patch件数、validity、diagnosticと一緒に比較する。画像生成や静的screenshotではなく構造化Specの決定的描画であり、自動適用はしない。選択案は従来どおりPatch Reviewへ送り、部分選択、lock、checksum、再validationを経て適用する。template parameterはF3.6、focus／keyboard／contrast auditは後続とする。

### Phase F3.6 — Parameterized Composite／Pattern（2026-07-19実装）

Design System catalogをschema version 5へ更新し、Composite／Patternへparameter schemaを追加する。parameterは安定key、表示label、string／number／boolean／enum、決定的default、required、最大文字数、数値範囲、enum optionと、template内の固定Component ID＋property targetだけを表現する。targetは既存Componentのproperty schemaに存在するfieldへ限定し、frontendにtemplate別parameterや展開先を二重定義しない。

KPI Card、Job Status、Log Viewer、CRUD Table、Timeline、Dashboard、Settings、Wizard、Launcherの全templateをparameter化する。title、metric／input／chart／tableのaccessible label、action label、help／empty text、初期値を挿入前に設定でき、defaultのままでも既存と同じ有効なSemantic Component treeになる。任意JSON Pointer、Binding、Event、式、HTML、handler code、Secret targetはparameter schemaに含めない。

frontendはschema駆動dialogで値を編集し、required、文字数、数値範囲、enumを挿入前に検査する。値は元template IDに対する宣言済みpropertyへ適用した後、既存tree全体と衝突しないIDへ再採番する。展開後はtemplate情報や実行可能式をSpecへ残さず、通常のComponent propertyとして既存Undo／Redo、dirty、Save、backend validationを通す。focus／keyboard／contrastの自動auditはF3.7で実装する。

### Phase F3.7 — Accessibility Audit／Keyboard Reorder（2026-07-19実装）

Design System catalogをschema version 6へ更新し、通常文字4.5:1、大きな文字3.0:1、touch target 44px、focus indicator 2pxを唯一の監査閾値として配信する。Auditは静的な「対応済み」flagではなく、Default Previewの実DOMを対象にcomputed styleとbounding boxを取得する。対象文字のforeground、最寄りのopaque background、継承opacityを合成してcontrast ratioを計算し、interactive controlは実寸、focus可能性、focus後のoutline／box-shadowを検査する。

Text Inputは副作用なしのread-only control、Workflow actionは実行handlerなしのbuttonとしてfocus可能にする。Eventを持つTable／Chartは適切なrole／accessible name／`tabIndex`／focus outlineをPreviewへ反映する。Auditはcontrast／focus／keyboard／touchごとの検査数と`A11Y_*` issueを表示するが、Workflow、Event、navigation、network、DB、LLM、Secret解決は行わない。accentのpreview mappingは全presetでwhite小文字との4.5:1を満たす値へ調整し、任意色のSpec保存は禁止を維持する。

Component Treeは非root itemへ`aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"`を付与し、Alt+↑／↓を既存兄弟移動操作へ接続する。Desktop drag、touch／pointer向けInspector Move、keyboard reorderは同じApplication Spec更新とUndo／Redo、dirty、明示Saveを使う。これによりF3のDesign System、property、全状態、structured data、Binding/Event、visual diff、parameterized template、a11y残件を完了した。

### Phase B1 — Platform Advisor／Incompatibility Preflight（2026-07-19実装）

framework registryは10候補ごとにSDK、feature、対応platformと`spec/source/localBuild/remoteBuild/package/signing/store/stability`を返す。Advisorは対象OS、offline、local file、tray、background、GPU、embedded server、store、優先言語、native feel、web reuse、package sizeを固定registryで採点し、全候補の順位、理由、制約、matrixを決定的に返す。blocking constraintをscoreで相殺せず、複数targetと利用者overrideを許可する。

`POST /application-builder/platform-advisor`と`POST /application-builder/preflight`はview権限下のread-only APIとする。PreflightはApplication Spec validation、platform coverage、SDK、Apple host、source generator availabilityを統合してtarget別matrixとstructured diagnosticを返す。executor、network、subprocess、filesystem write、Secret解決は呼ばず、responseの`sideEffects`でも全項目falseを保証する。

frontendは推薦を初期選択し、複数候補の選択、coverage不足、保存前Preflight、target overrideを同じ画面で扱う。B1ではsource generatorを全候補`unavailable`に維持し、`SOURCE_GENERATOR_UNAVAILABLE`を表示する。generate/build/publish button、build ID、artifactを作らない。次はB2としてC# Console／ASP.NETのdeterministic source generator基盤へ進む。

### Phase B2.1 — Core C# Console Source Generator（2026-07-19実装）

最初のsource available targetを`csharp-console`のlinux／windowsとする。保存済みApplication Specと解決済みWorkflow IRを入力に、C# `net8.0` CLI、generated runtime、Application metadata、user-owned Extension境界、non-secret Config、NuGet追加依存のないself-test projectを生成する。対応nodeは`trigger`、`util.wait`、制限付き`util.now`、`var.set`、制限付き`string.op`、`output.render`、`signal.display`で、topological orderを元definition順のstable tie-break付きで確定する。

`.controldeck/generation-manifest.json`はgenerator ID/version、Spec checksum、Workflow checksum、target ID、framework、source checksum、fileごとのSHA-256／byte数／kind、Managed／Extension／Config一覧を持つ。archiveはpathでsortし、ZIP timestampを1980-01-01、permissionを0644、compressionをstoredへ固定する。同じSpec、Workflow snapshot、target、generator versionからarchiveを含む同じbyte列を返す。manifest自身は再帰checksum対象にせず、manifest以外の全source fileをsource checksumへ含める。

Previewはfile一覧、manifest、checksum、side-effect全falseを返す。Downloadはedit権限とCSRFを必須にし、メモリ内で同じbundleを生成して`application/zip`を返し、target、generator、checksum、file数、byte数だけを監査する。source本文、config本文、Secret名／値は監査metadataへ含めない。生成器はfilesystem、executor、network、subprocess、Secret storeを呼ばない。

Secret参照、複数Workflow、未対応node、branch、retry／timeout／continue、未対応node configは生成前errorとする。これらをruntime fallbackや成功するstubに置換しない。build／package／signingは引き続きunavailableであり、SDK検出はPreflight表示だけに使う。次のB2.2でbranch、execution policy、native nodeを増やし、その後ASP.NET source generatorと隔離buildへ進む。

### Phase B2.2 — Branch／Merge／Execution Policy Runtime（2026-07-19実装）

generator 1.1.0は`condition.if`と`control.merge`をnativeへ昇格する。generated schedulerはtriggerだけをrootとして開始し、最大4並列、最初のlive入力、`join=all`、dead signal、skip伝播を扱う。conditionはtrue／false、通常成功はerror／timeout以外、`on_error=branch`はerrorまたはtimeoutをliveにする。timeout edgeがない既存flowではerror edgeへtimeoutを送る互換規則も維持する。

mergeの`wait_all/collect`は全edge解決後に1つ以上liveなら実行し、`first_success`は最初の成功、`first_complete`は最初のlive完了、`quorum`は必要成功数で実行する。merge inputはnode ID、`SUCCEEDED/FAILED/TIMED_OUT/SKIPPED`、型付きoutputを持ち、values/count/succeeded/valueを既存node contractと同形で返す。definition順は同時完了時のstable tie-breakと最終output収集順にだけ使い、独立branchの並列性を失わない。

各nodeはretry最大5回、wait最大300秒、timeout 0.1〜7200秒を持つ。generated runtime既定timeoutは通常120秒、wait 3700秒で、retry有効かつwait省略時は5秒。attemptごとのlinked cancellationでtimeout後のTaskを残さず、stopは全running taskをcancel／await、continueはfailure outputを通常edgeへ、branchはerror／timeout edgeへ渡す。

human approval、named output variable、loop body/done、Secret injection、未対応branch値はblockingのままにする。source生成中にruntimeを実行せず、build availabilityも変更しない。次はB2.3でnamed variableと`data.transform/template/filter/aggregate`等のpure deterministic nodeを追加し、side-effect nodeはpath／network／credential境界を別設計してから扱う。

### Phase B2.3 — Named Variable／Pure Data Runtime（2026-07-19実装）

generator 1.2.0は成功nodeの`output_var`をgenerated scheduler内のnamed variableへ保存し、`{{vars.name.path}}`を通常node referenceと同じ非再帰dot path resolverで解決する。変数はnode outcome確定後、outgoing edge伝播前に公開し、並列branch間で未確定値を推測しない。`data.template`だけは解決済みdataを疑似contextとして渡し、`{{data.path}}`を許可する。

native対象へ`data.transform`、`data.template`、`data.filter`、`data.aggregate`を追加する。transformは`json_parse/json_get/json_set`、templateはtext／JSON、filterはexists／truthy／同値／contains／数値比較、unique、stable sort、limit、aggregateはcount／sum／avg／min／maxとgroupを扱う。UTF-8入力／template／出力は2MiB、filter／aggregate arrayは10,000件とし、JSON path、boolとnumberの区別、null、group入力順を既存Python executor contractへ合わせる。

portableな標準libraryだけで同一contractを保証できないJSON Schema validationとCSV相互変換はoperation単位でblocking diagnosticにする。未知operation、format、sort orderも生成時errorとし、成功stubや実行時fallbackにしない。source生成は引き続きexecutor、filesystem、network、subprocess、Secret storeを呼ばず、build availabilityも変更しない。次はB2.4でloop／残りpure nodeの境界を固め、その後ASP.NET source generatorと隔離buildへ進む。

### Phase B2.4 — Nested Loop Runtime（2026-07-19実装）

generator 1.3.0は`control.loop`のcount／foreachとbody／done edgeをnativeへ昇格する。outer graphと各iterationは同じrecursive DAG runnerを使うが、outcome／named variable dictionaryは反復開始時にsnapshotする。loop nodeは既存engine同様に通常nodeのretry／timeout／4並列permitを消費せず、body nodeだけが全graph共通の4並列permitを使う。

countは1〜100、foreachはJSON array、単一JSON値、JSONでない場合は非空行listを入力として最大100件、parallelは1〜5とする。parallelは入力順のbatchで実行・集約し、各resultはindex、item、iterationで変更されたnode outputを持つ。完了後は最後の反復contextとnamed variableをparentへ反映し、done側からbody nodeを参照できる既存互換を維持する。loop outcomeは最終index／item、total、done、resultsを返す。

loop完了時にbody edgeをdeadとして送ると最終反復outputをskipで上書きするため、outer schedulerではbody edge自体を再処理しない。doneと無指定edgeだけを通常成功として伝播する。body内のbranch、merge、nested loop、retry／timeoutは同じruntime契約を再利用し、stop errorは全iterationとouter graphをcancel／awaitする。未知mode、非整数count／parallel、loop以外からのbody／done edgeはpreflight errorとする。C# sourceは`#nullable enable`を持ち、net8.0 warning-as-error buildを通す。

標準libraryだけでPython実装と同じcontractを保証できないMarkdown、regex方言、JSON Schema、CSV、Secret、filesystem／network等のside-effect nodeは対応したふりをせずmanual／blockingのままにする。次は正規Phase境界どおりPhase C1でASP.NET API／health／OpenAPI source基盤を追加し、Consoleと共通のWorkflow runtimeを再利用する。Blazor／ReactのSemantic Component UI生成はD/E〜G2より前にsource available扱いしない。

### Phase C1 — Typed API／ASP.NET API Source（2026-07-19実装）

ASP.NET generatorの入力は任意C# handlerではなく、Application Specのtyped `apiEndpoints`／`backgroundJobs`とWorkflow bindingに限定する。endpointはPOST path、sync／async、inherit／anonymous認証、request／response JSON Schema、timeoutを持ち、pathは固定segmentと型名を持たない`{parameter}`だけを許可する。jobはWorkflow binding、manual／interval／daily／cron、schedule、enabled、timeoutを持つ。未知fieldはSpec v1のround-trip要件に従い保持する。

保存前に正規化route重複、Workflow参照、path parameter重複、job scheduleを検査し、anonymousは明示指定だけを許可してsecurity warningを返す。local認証を未認証endpointへ落とすfallbackは禁止する。

generator 0.1.0は13-fileの決定的ZIPとしてnet8.0 Web／self-test project、Dockerfile、README、OpenAPI 3.1、checksum manifestを生成する。hostは2MiB request上限、health、API key固定時間比較、route input、sync timeoutを持つ。async endpointは最大1,000件のin-memory job、status、SSE、DELETE cancel、timeout／Application shutdown cancelを持ち、作成endpointのanonymous／API key方針をjob操作へ保存して引き継ぐ。生成はexecutor、network、subprocess、filesystem、Secret storeを呼ばない。

`aspnet-blazor`のsource availableはこのAPI範囲だけを意味する。GUI Page／Semantic ComponentはD/E〜G2、EntityはD/E、scheduled background job／永続queueとruntime JSON Schema validationはC2以降とし、該当Specはblocking diagnosticにする。`aspnet-react`はGUI host境界が完成するまでunavailableを維持する。

### Phase C2 — JSON Schema／Durable Background Job Runtime（2026-07-20実装）

generator 0.2.0はrequest bodyへrouteを混ぜる前にschemaを検査し、sync／asyncのWorkflow結果にもresponse schemaを強制する。OpenAPIへ同じschemaを出力するだけの見せかけ対応は禁止する。dependencyを追加せず、type、enum／const、properties／required／additionalProperties、items／uniqueItems、長さ／件数、数値制約、allOf／anyOf／oneOf／notを生成runtimeで保証する。最大64深度、100診断、10,000 array item、2MiB bodyを上限とする。ECMA／.NETで意味がずれるpattern、format、remote/local `$ref`等は`API_SCHEMA_KEYWORD_UNSUPPORTED`で生成前停止する。

background jobはmanual／interval／daily／5-field cron、IANA time zone、固定input、timeout、enabled、`skip/queue-one`、`skip/run-once`をtyped contractとする。manualはAPI key方針を継承するrun API、scheduleは生成ASP.NET自身の`BackgroundService`で同じWorkflow runtimeを呼ぶ。cronは数値と月／曜日名、list、range、step、wrap range、標準のday-of-month／day-of-week OR規則を扱い、UTC minuteをtime zoneへ変換してDSTを評価する。

schedule stateは明示data root直下の固定`schedule-state.json`へ1MiB上限でatomic保存する。last start／evaluation、running pending、queue-oneを保持し、通常再起動で同じslotを二重実行せず、実行中crashはat-least-onceとして1回再投入する。manual overlapは409、schedule overlapはskipまたは最大1件queueとする。永続queue／履歴DB全体を装うのではなく、このC2 stateが保証する範囲をmanifest／READMEへ明示する。次はD1のtyped Entity／SQLite migration／CRUD source境界であり、GUI生成はその後も同じApplication Specを利用する。

### Phase D1 — Typed Entity／SQLite Migration／CRUD Source（2026-07-20実装）

Entityは自由形式dictではなく、ID／table、1〜100 field、string／integer／number／boolean／datetime／JSON、nullable、`hasDefault`で明示するdefault、maxLength、unique／index、別Entityのgenerator管理UUIDへの外部キー、delete policy、CRUD公開範囲を持つ。`id/createdAt/updatedAt`はgenerator管理列とし、CRUDは既定無効、認証はapplicationのnone／api-keyを継承する。Entity同士、API／job管理routeとの衝突、default／relationの型不整合は生成前に停止する。

generator 0.3.0はEntityがあるprojectだけに`Microsoft.Data.Sqlite/8.0.29`と`Entities.generated.cs`を追加する。database pathは正規化したapplication data root直下の固定名とし、WAL、foreign key、transactional startup migrationを使う。migrationはtable／列／indexの追加だけを行い、defaultなしrequired列、既存型／nullability変更、既存列へのrelation後付けを安全に再構成したふりをせず停止する。Spec checksumとEntity IDの適用記録は履歴情報であり、schema互換性検査を省略する根拠にはしない。

CRUDは全SQL identifierを検証済みmetadataから引用し、値はparameterだけで渡す。2MiB JSON object、UUID ID、型／長さ／offset datetime、unknown field、unique／foreign keyを検査し、list上限は100件とする。deleteは削除と同じtransactionで固定audit tableへaction、Entity ID、resource ID、時刻だけを残す。OpenAPIは有効operationだけを公開する。生成runtimeはControlDeck DBやaudit serviceへ接続せず、独立アプリのdata rootだけを所有する。

Linux／Windowsで生成可能なtargetはC# ConsoleとASP.NET Coreの2系統を保証すればよく、全言語／全advisor候補をsource availableへ昇格しない。次のD2は同じtyped contractを編集するEntity／relation／CRUD GUIとCRUD Table bindingであり、GUI source生成はE以降とする。

### Phase D2 — Entity Editor／CRUD Table Binding（2026-07-20実装）

Entity editorは別Project editorとしてSpec全体を上書きせず、Design editorと同じlocal Spec、50段Undo／Redo、dirty state、単一atomic saveを使う。EntityとDesignの片方が未保存の時にもう片方だけを古いsnapshotから保存する経路を作らない。320pxでは縦積み、desktopではEntity listとfield inspectorの2列とし、全controlを44px以上にする。Entity削除だけを破壊的操作として確認し、field追加／設定変更には確認dialogを出さない。

field type、nullable、明示default、maxLength、unique／index、Entity ID relation／onDeleteとCRUD operation／base pathをschema準拠controlで編集する。local validationは即時feedback用であり、保存時のPydantic／compiler validationを置換しない。最初のEntity追加時だけdatabase=sqliteを設定し、全Entity削除時に既存database設定を暗黙変更しない。

Component bindingのentity sourceは自由文字列を廃止し、現在のEntity collectionと`id/createdAt/updatedAt`を含むfield候補だけを選択する。保存済みSpecは`entity:EntityId`または`entity:EntityId.fieldId`とし、backend compilerもEntity／fieldの存在を再検査する。CRUD Table compositeは既存data.tableを再利用し、このbindingからD1のCRUD schemaへ接続する。次はE1でこのSemantic Component treeとbindingを実Blazor UI sourceへ生成する。

### Phase E1 — Semantic Component／Entity Binding Blazor Source（2026-07-20実装）

ASP.NET generator 0.4.0はPageごとのRazor route、App Router／navigation、responsive CSS、固定JavaScriptを決定的に生成する。対象はlayout stack／row／grid／card、text／markdown／metric／text input、Entity collectionへbindingしたdata tableに限定する。Data TableはD1 Entity CRUDのlist operationだけをanonymous fetchし、値はDOM `textContent`へ設定する。Spec文字列はRazor text／attributeごとにescapeし、任意HTML、任意JavaScript、`innerHTML`を生成しない。

event、Workflow action、chart、Entity fieldをcollectionとして扱うbinding、list未公開Entityはblocking diagnosticとする。browser session認証adapterがないためPage付きGUIは明示`authentication: none`に限定し、API key／local認証を安全でない仮実装へ落とさない。生成GUIはstatic SSR＋read-only listであり、mutation formと認証adapterはE2へ送る。Antiforgeryのdata-protection keyは生成process内repositoryだけに保持し、user profileへ暗黙の鍵fileを残さない。

### Phase E2 — Entity Mutation／Browser Session（2026-07-20実装）

Data Tableはcreate／update／deleteを個別typed propertyで明示し、Entity collection binding先のCRUD公開範囲と生成前に照合する。generatorはEntity field型、nullable、default、maxLengthからformを生成し、createとupdateは同じfield contract、deleteはrow IDと確認dialogを使う。row actionは常時横並びにせずMore menuへ集約し、主操作はAdd item一つ、赤色はDeleteだけとする。mutation後は同じlist endpointを再読込し、user値をHTMLへ挿入せず`textContent`だけへ設定する。

`authentication: api-key`のPageはAPI keyを成果物へ埋め込まず、loopback HTTPまたはHTTPSのsign-in endpointで環境変数と固定時間比較する。成功時はrandom tokenのhashだけを最大1,000件／12時間process memoryへ保持し、HttpOnly／SameSite=Strict cookieを返す。unsafe requestはcustom same-origin headerを追加要求し、login attempt／source／bodyを固定上限へ閉じる。sessionは再起動で失効し、API clientの`X-API-Key`互換は維持する。CSP／frame拒否／nosniff／no-referrerをhost全体へ設定し、data-protection keyもprocess外へ永続化しない。次はE3でWorkflow trigger form、typed result、navigate／state eventの生成境界を扱う。

### Phase E3 — Workflow Form／Typed Result／Navigation（2026-07-20実装）

`action.workflow-run`は自由なclient handlerやWorkflow直接実行を生成せず、Application SpecのWorkflow bindingと、同じWorkflow IDを参照する保存済み同期API endpointへ接続する。endpoint IDを明示するか、候補が1件だけなら自動解決する。binding／endpoint欠落、不一致、複数候補、async、route parameterはblocking diagnosticとし、browser側がjob pollingやroute値を推測しない。App StudioはWorkflow bindingとmatching sync endpointをselectで示し、既存の不正値は`unavailable`としてround-tripを維持する。

request schemaはobject propertiesかつ最大50 fieldに限定し、string／enum／integer／number／boolean／object／arrayをschema-driven controlへ変換する。required、min/max length、数値min/max、title／descriptionを写し、JSON Schema required booleanはfalseを禁止するHTML required checkboxへ変換しない。object／arrayはparse後の実型を確認する。server側のC2 JSON Schema validatorが正であり、pattern／composition等の全制約をbrowser validationだけへ委ねない。

responseは実JSON型に応じてprimitive、description list、object-array tableへ描画し、すべてDOM `textContent`を使う。object 1,000 field、array 1,000 row／20 columnを上限とし、`innerHTML`やSpec由来scriptを生成しない。eventは`action.workflow-run`のsuccess／errorから既存PageへのNavigateだけを生成し、compilerがtarget存在を検査した後、固定routeをdata attributeへ書く。HTTP errorとnetwork errorはerror routeへ送る。state schema、lifetime、binding consumerが未定義の`state-set`と、再帰実行になり得るWorkflow eventはblockingのままにし、次Phaseでtyped state／query binding contractを先に定義する。

### Phase E4 — Typed Client State／Focused Workspace UX（2026-07-20実装）

Application Specの`clientState`はID、JSON型、初期値、nullableを持つtyped contractとする。個別64KiB／合計256KiB、有限number、型一致を保存前に検査する。`state:` bindingは存在する宣言を参照し、Text／Markdownは全JSON型、Metricはscalar、Text Inputはstringだけをconsumerとして許可する。`state-set`はconsumerがあり、設定値またはWorkflow response schemaが宣言型と一致する場合だけ生成する。

生成browser runtimeは初期値をescaped data attributeから読み込み、process内のmemory mapだけで保持する。表示は`textContent`、入力はvalueへ設定し、HTMLやSpec由来scriptを実行しない。Text InputのchangeとWorkflow success responseをstateへ反映し、HTTP／network errorは外部response本文や例外文を漏らさない固定objectへ変換する。reloadで初期値へ戻し、localStorageや永続DBを暗黙利用しない。query bindingは取得元、cache、再読込、loading／empty／errorをtyped contractとして定義する次Phaseへ送る。

AppBuilder本体は機能単位の縦長画面ではなく、利用目的に合わせて`Create`／`Target`／`Export`／`Review`へ分割する。Create内はCanvasとDataを切り替え、PCはAdd／Canvas／Inspector、mobileはCanvas＋下部Add／Layers／InspectボタンとSafe Area対応bottom sheetを用いる。詳細binding、interaction、advanced JSON／AI lockは段階開示し、同じApplication Spec、50段Undo／Redo、dirty state、単一Saveを全editorで共有する。workspaceを切り替えても未保存draftを破棄しない。

### Phase E5 — Typed Entity Query（2026-07-20実装）

`queries`は画面から実行するread-only collection取得をcomponentから分離する。E5のsourceは公開済みCRUD `list`を持つEntityに限定し、Query ID、Entity ID、1〜100件のlimit、auto-load、`network-only`／memory cache、0〜3,600秒のstale timeを保存する。compilerはEntity／list／consumer／columnを参照検査し、`query:` collectionはData Tableだけへ許可する。既存の直接`entity:` bindingは後方互換として残すが、新規画面ではQueryを選ぶことで取得方針を一か所に集約する。

生成browser runtimeはQuery IDごとのpending requestを共有し、memory cacheは取得時刻とstale timeで再利用可否を決める。初回はloadingまたは明示的なnot-loaded、0件はempty、失敗は外部response本文を含まない固定messageとし、Refreshでcacheを破棄して再試行する。Entity mutation後も同じQueryを強制再取得する。row値は`textContent`だけで描画し、Query結果をlocalStorageへ永続化しない。API endpoint query、filter／sort／cursorまたはoffset paginationはrequest／response型と上限を別途定義する次Phaseへ送る。

### Phase E6 — Typed API Query／Filter／Sort／Pagination（2026-07-20実装）

Query sourceはEntity collectionと同期API endpointの2種とする。Entity Queryは最大20 filter、最大3 sort、`none|offset` paginationを持つ。field型ごとにoperatorを制限し、stringはeq／ne／contains／starts-with、number／integer／datetimeはeq／ne／gt／gte／lt／lte、booleanはeq／ne、nullable fieldはis-nullを追加する。compilerはfield、operator、値型、sort重複、CRUD list公開を検査する。生成SQLite runtimeも同じ上限と型を再検査し、columnは生成metadataのwhitelistだけから選択、値はすべてparameter binding、LIKE wildcardはescapeする。明示sortの末尾へIDを加えてoffset間の順序を決定的にする。

API Queryはroute parameterを持たない同期endpoint、最大64KiBの固定JSON object input、dotted result pathを持つ。inputはendpoint request JSON Schemaへ、result pathはresponse JSON Schema内のobject arrayへ生成前に照合する。API固有のfilter／sort／paginationはendpoint request schemaと固定inputへ明示し、Query側へ二重定義しない。browser runtimeは固定inputをPOSTし、result pathを安全に辿って配列だけを受理する。Entity/APIともQuery IDとoffsetをcache keyにし、loading／empty／固定error、Refresh、Previous／Nextを同じTable statusへ統合する。response本文や例外詳細は表示せず、row値は`textContent`だけを用いる。次はSecret injection／side-effect nodeのpath・network・credential境界を設計し、その後に隔離buildへ進む。

### Phase E7／B2.5 — Secret Injection／Bounded Side-effect Source（2026-07-20実装）

CompilerはWorkflow内のSecret参照名を決定的な`SECRET_001…`へ置換してgeneratorへ渡し、実名は生成source、manifest、README、監査、最終出力へ含めない。値は生成アプリ起動時の`CONTROLDECK_SECRET_001…`だけから読み、欠落、未宣言alias、64KiB超を停止する。SecretはHTTP header／bodyだけへ許可し、URL、path、制御、出力への参照と秘密らしいliteral credentialを生成前diagnosticで拒否する。最終JSONとfile write内容は読み込んだ値を長い順に伏せ字化する。source Preview／ZIP生成時はSecret store、executor、network、filesystem、subprocessを呼ばない。

`http.request`は2048文字以内の固定absolute URL、HTTPSまたはloopback HTTP、GET／POST／PUT／PATCH／DELETE／HEADに限定する。userinfo、fragment、秘密らしいquery、restricted／改行headerを拒否し、redirect、cookieを無効化する。header 32KiB、個別値8KiB、request 2MiB、response 4MiBを上限とし、監査にはmethod、origin、request byte数、結果だけを最大2MiB＋1世代rotationで記録する。header、body、query、response、Secret値は記録しない。

`file.read/write/exists/glob`は既存の`CONTROLDECK_APP_WORK_ROOT`配下だけを対象とし、絶対path、drive path、`..` escape、途中symlinkを静的・runtimeの両方で拒否する。writeは2MiB、append後4MiB、overwriteは一時fileからatomic move、globは固定pattern、最大1,000結果／100,000 scanとする。ASP.NETでSecretまたはside effectを含むWorkflowはapplicationのAPI-key認証と非anonymous endpointを必須にする。Console／ASP.NETは同一runtimeを再利用し、Linux／Windows向け2 generatorだけを正式source targetとして維持する。次はsystemd user transient unit、SDK allowlist、resolved root、resource／timeout／cancel／log redactionを持つ隔離buildへ進む。

### Phase B3 — Isolated Build／Self-test（2026-07-20実装）

保存済みApplication SpecとWorkflow snapshotから決定的に再生成したSource ZIPだけをbuild入力とし、C# Console／ASP.NET Coreの両targetを一時`systemd-run --user` unitでoffline restore、warning-as-error build、生成self-testする。SDKは設定または明示環境で選択した実行可能な`dotnet`だけをallowlistとし、worker Pythonはvenv launcherのsymlink semanticsを保持する。Web processはSDK processを子processとして所有しない。

unitは`NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome=read-only`、build ID専用rootだけの`ReadWritePaths`、`IPAddressDeny=any`、`RestrictAddressFamilies=AF_UNIX`、2GiB memory、128 tasks、CPU 200%、60〜3,600秒timeoutを固定argvで設定する。workerはIPv4／IPv6 socket生成を実測してfail closedとし、SDK／生成self-testへは専用HOME／TMP／NuGet領域、固定PATH／localeだけを渡す。Control Deckの環境、設定path、Secret、`PYTHONPATH`は継承しない。

入力ZIPは64MiB、500 file、展開128MiB、relative regular file、重複／暗号化／symlink／escape拒否とする。build root、unit名、artifact pathをbuild IDとapplication-owned rootへ結び、source ZIPと最大50件のregular binaryだけをmetadata化する。状態はpreparing／generating／restoring／building／testing／completed／failed／cancelled／timed_out／interruptedをDBとatomic state fileで追跡し、同時2件・Projectごと1件、cancel、1MiB redacted journal、artifact checksum／認証download、破壊的削除と監査を持つ。ID取得後の最初のcommitにunit名と期待rootを保存し、作成途中で停止したrootなし記録も安全に削除できる。

UIはExport workspaceの`Build & test`を主操作1個とし、isolation／network／SDK／並列数、phase indicator、Cancel、成果物、3点menu内log／Deleteを段階表示する。320pxでは縦積み、PCでは既存workspace内cardとし、赤は失敗だけ、削除だけ確認dialogを使う。Linux／Windows向け生成選択肢はC# Console／ASP.NET Coreの2系統に限定し、全言語対応を装わない。

### Workflow契約ベース自動アプリ化

WorkflowからProjectを作る際は、trigger input schemaとtyped output schemaを同一snapshotから取得し、同期Workflow endpoint、API-key認証、型別入力、結果renderer、navigation、responsive Pageを含むApplication Specを初期保存する。文字列enumはselect、文字列はtext、integer／numberはnumber、booleanはboolean、object／arrayはJSON controlへ写像する。Editorだけの見せかけではなく、ASP.NET generatorが同じrequest／response schemaから実フォームを生成することを必須とする。

このbaselineはLLMなしでも生成・実行できる決定的なfallbackではなく、常に成立する正規の初期案である。Advisorは抽出した入出力とcontrol選択を示し、主要操作を生成・動作確認へ置く。AI再検討時はApplication Specに加えてWorkflow契約、binding、endpoint wiringをpromptへ明示し、Simple／Balanced／Denseのschema-constrained Patchを生成する。AI案は既存動作を保つ不変条件を持ち、静的検証とvisual diffを経て利用者が選択適用する。model停止、invalid Patch、提案拒否時もbaselineとCanvas手動編集を失わない。

## 12. Phase A acceptance

- legacy definition → IRのinput/output/branch/merge/retry/timeout/secret名/side effect。
- TypeRef parse/serialize/assignability/mapping、unresolved/type mismatch diagnostic。
- Spec duplicate/ref/binding/security/target validation。
- capability registryをfrontendへ二重定義しない。
- Project CRUD/audit/RBAC/migration。
- validateがexecutor、LLM、subprocess、network、secret解決を呼ばない。
- `[アプリ化]`からprojectを作り、診断と実装状態を表示できる。
- build/generate UIと成功dummyが存在しない。
- backend全test、frontend build、320/390/768/1280 E2E、実サービス確認。

最終Phaseでは、部品配置、keyboard reorder、3案比較、partial apply、lock、atomic undo、320〜1920 preview、全状態preview、platform recommendation/override、incompatibility preflight、deterministic generation、secret非混入を完了条件へ追加する。

## 13. 推奨値・ゼロ設定・インラインガイダンス（2026-07-19追加）

設定項目を増やすほど利用者へ判断を押し付けないよう、すべてのnode/component schemaは次を表現できるようにする。

- `default`: executorが省略時にも使う決定的な初期値
- `recommended`: 用途・side effect・targetを踏まえた推奨値
- `reason`: 推奨理由、性能・安全性・コスト上のtrade-off
- `examples`: 最小例、標準例、他nodeと組み合わせた実用例
- `help`: 詳細な使い方、入力契約、出力契約、失敗時の挙動
- `uiHints.variablePicker`: 上流、trigger、workflow、loop、error、secret等の候補を型付きで検索するeditor

新規node追加時は必須値を空欄の巨大formへせず、危険な対象URL、path、secret、app ID等を除いて安全な推奨値を初期投入する。
network nodeはtimeout/retry/backoff、LLMはendpoint/modelの稼働候補、検索は件数/depth、loopは上限、outputは名前/renderer/schemaを推奨する。
副作用を伴う対象値を勝手に推測して実行してはならない。

入力editorは単なるtextareaではなく、入力先が要求する型を起点に、直前node、他の上流node、trigger input、workflow variable、
loop item、error context、secret name、前回実行値を検索・preview・挿入できるようにする。不一致は候補から除外または警告し、
安全な変換候補か`data.transform`追加を提案する。変数候補とhelpはbackend metadata/schemaを正とし、frontendへ再定義しない。

Phase Aではmetadataが`default/recommended/reason/help/examples/input/output/security`を返す互換基盤を追加する。既存Workflow Inspectorの
schema駆動化、初期値migration、型付き変数editor、インライン構成例はApplication Builder PRへ混在させず、独立Workflow UX PRで実装する。
