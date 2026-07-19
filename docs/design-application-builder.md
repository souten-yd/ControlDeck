# ControlDeck Application Builder 詳細実装仕様

最終更新: 2026-07-19
状態: 要求整理・コード監査完了、Phase A 実装待ち

## 1. 目的と不変条件

既存Workflowから、ControlDeckに依存せず実行・配布できるアプリケーションを決定的に生成する。Workflow、データモデル、GUI、API、永続化、background jobを構造化仕様として編集し、LLMは仕様提案とJSON Patchだけを行う。

> 部品を選ぶ → 並べ替える → AI案を比較する → PC/Tablet/Mobileをpreviewする → 直接操作または文章で修正する → 差分を選択して適用する → 検証する → 対象platform向けに生成する

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
