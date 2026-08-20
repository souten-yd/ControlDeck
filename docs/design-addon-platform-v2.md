# Add-on Platform v2 詳細設計

最終更新: 2026-08-20  
状態: 計画 / Plugin SDK v1 後方互換拡張

## 1. 目的

現行 Plugin SDK v1 は、安全な外部 Web アプリを ControlDeck の navigation に公開するための最小 SDK として維持する。
一方、Media Forge のような高度なローカルサービスを **本当にプラグインとして** 統合するため、ControlDeck 本体にはアドイン固有コードではなく、将来の別アドインでも再利用できる汎用 Extension Host を追加する。

原則:

> ControlDeck は「拡張機能を安全に受け入れる方法」を知る。各アドインの業務ロジックは知らない。

Media Forge は最初の主要ユースケースだが、v2 の API/DB/UI contract に `media` 固有概念を埋め込まない。

---

## 2. 現行 v1 を残す理由

Plugin SDK v1 の `navigation` のみという境界は、単純な外部 GUI に対して安全で理解しやすい。

v2 の導入に伴って v1 を壊さない。

```text
api_version=1
  -> external navigation plugin
  -> 現行挙動を維持

api_version=2
  -> isolated service add-on
  -> declarative contributions + scoped host bridge
```

未知 api_version / capability / contribution は fail closed。

---

## 3. 批判的検討

### 3.1 「ControlDeckにMedia機能を実装し、enabled時だけ表示」は採用しない

UIだけ隠しても実装・依存・migration・routeが本体に残れば実質モノリスである。

ControlDeck側に置いてよいのは **汎用 contribution slot / bridge / scheduler / permission / job integration** まで。

### 3.2 「外部URLを新しいタブで開くだけ」も不足

Jobs、Files、Project Lab、Agent、Workflow、GPU管理、通知、モバイルUXとの統合が弱すぎる。

### 3.3 任意Python/JSを本体プロセスへimportしない

プラグインの自由度より、依存分離・権限境界・障害分離を優先する。

高度なアドインは out-of-process service として実行し、ControlDeck とは versioned contract で接続する。

---

## 4. Add-on v2 manifest

概念例:

```json
{
  "api_version": "2",
  "id": "example-addon",
  "name": "Example Add-on",
  "version": "1.0.0",
  "publisher": "example",
  "runtime": {
    "kind": "external-service",
    "health_url": "http://127.0.0.1:9130/health"
  },
  "contributions": {
    "navigation": [],
    "embedded_views": [],
    "commands": [],
    "quick_actions": [],
    "settings": [],
    "workflow_executors": [],
    "agent_tools": [],
    "context_actions": []
  },
  "host_capabilities": []
}
```

manifest 自体は宣言であり、任意コードを含めない。

---

## 5. Effective Contribution Registry

インストール済み manifest と「現在有効な contribution」を分離する。

有効条件:

```text
installed
AND enabled
AND manifest valid
AND host contract compatible
AND contribution permission valid
AND core runtime health policy satisfied
```

`/api/v1/meta` に公開してよい最小情報と、ログイン後だけ取得できる詳細 extension metadata を分ける。

推奨:

```text
GET /api/v1/meta
  -> ログイン前に必要な最小情報のみ

GET /api/v1/addons/effective
  -> 認証後
  -> navigation / commands / settings / embedded views / capabilities
```

無効化した contribution は registry から消える。CSSで隠すだけは禁止。

---

## 6. UI contribution

### 6.1 ControlDeck がネイティブ描画するもの

- sidebar navigation
- mobile navigation / More
- Command Palette
- Quick Actions
- settings entry
- status/badge
- notification action

これらは manifest/runtime metadata から ControlDeck 自身が描画する。

### 6.2 full workspace

高度な UI は `/addon-view/{plugin_id}/{view_id}` 等の host route で sandboxed embedded view として表示する。

見た目は ControlDeck shell 内だが、アドインへ通常の ControlDeck session cookie や DOM access を与えない。

### 6.3 design-token contract

private React component を plugin API にしない。

安定した少数 token のみ渡す:

```text
theme
resolved color scheme
accent
locale
safe-area
ui density
contract version
```

---

## 7. Host Bridge

ブラウザ側は MessageChannel/postMessage 等の明示bridgeを使う。

例:

```text
host.context.get
host.theme.get
host.route.open
host.file.pick
host.file.export
host.project.pick
host.job.open
host.notification.show
host.permission.has
```

サービス側も unrestricted ControlDeck API token を渡さず capability-scoped API を用意する。

```text
host.files.stage_read
host.files.commit_write
host.projects.get_context
host.jobs.register_remote
host.jobs.update_remote
host.resources.acquire
host.resources.renew
host.resources.release
```

すべて:

- schema validation
- plugin ID binding
- user/role permission check
- capability check
- audit

を通す。

---

## 8. Permission contribution

将来的には plugin namespaced permission を扱う。

例:

```text
plugin:media-forge:view
plugin:media-forge:generate
plugin:media-forge:manage-models
```

または UI 表示用 alias と内部 stable tuple:

```text
(plugin_id, permission_id)
```

を持つ。

### 注意

plugin uninstall 後にRBAC grantが永久に core permission として残らないようにする。

推奨:

- permission definition は plugin namespace で保持
- plugin absent/disabled 時は inactive
- reinstall 時に同じ publisher/id/signing identity が確認できた場合のみ既存grant再利用を検討

v2初期で汎用permission contributionを実装しない場合は、必要最小限のhost capability grantで開始してもよい。

---

## 9. Lifecycle

### install

- manifest validation
- safe managed copy
- installed/disabled
- runtime実体のインストールとは分離可能

### enable

1. manifest再検証
2. runtime health確認
3. requested host capabilities検証
4. scoped session/credential準備
5. effective contributions登録
6. UI/agent/workflow discoveryに反映

### disable

1. 新規呼出し停止
2. plugin session revoke
3. pending resource lease取消
4. effective contribution撤去
5. background host calls停止
6. plugin data/models/assetsは保持

### uninstall

- contribution/credential/state撤去
- ControlDeck管理下manifest削除
- 外部アドイン本体・データはデフォルトでは削除しない
- `delete plugin data` は将来別明示操作

---

## 10. Jobs integration

ControlDeck Jobs をユーザー向け統一ジョブ画面とする。

アドインは remote job adapter で正規化した状態だけ公開可能にする。

標準状態候補:

```text
queued
waiting_dependency
waiting_resource
starting
running
postprocessing
validating
succeeded
failed
canceled
interrupted
```

高頻度内部イベントを毎回ControlDeck DBへ複製しない。

---

## 11. Workflow contribution

任意Python executor importではなく remote executor contract を採用する。

ControlDeck owns:

- node catalog integration
- permission
- template expansion
- timeout
- cancellation
- dry-run
- job correlation

Plugin owns:

- domain execution

無効化時は既存workflow定義を削除せず `unavailable` 表示。

---

## 12. Agent tool contribution

OpenCode/Codex/OMO等へ、enabled + healthy + authorized のアドインtoolだけ公開する。

Tool schemaはアドインから取得して正規化・サイズ制限・検証し、ControlDeck agent bridge が中継する。

アドインtool経由でも Files/Project 権限を迂回できない。

---

## 13. File / Project grants

ローカルサービスだからといって arbitrary filesystem access を許可しない。

推奨:

- staged file
- temporary capability handle
- scoped validated path grant

path grant は:

```text
plugin_id
user_id/project_id
realpath
mode=read|write|readwrite
expiry
```

を持ち、symlink escapeを再検証する。

---

## 14. AI Resource Broker integration

GPUを使うadd-on向けに Add-on v2 は `ai.resource_lease` capability を定義する。

実体のスケジューラは `docs/design-ai-resource-broker.md` に分離する。

Add-on Platformは acquire/renew/release と job correlation だけを規定し、LLM token/KVや画像 diffusion step のような domain detail は持たない。

---

## 15. Health / partial degradation

`enabled` と `healthy` を混同しない。

```text
enabled + healthy
enabled + degraded
enabled + unavailable
```

アドインの一部capabilityだけ unavailable でも、workspace全体を消す必要はない。

例: Media Forge で imageは使えるが video workerが未導入。

ControlDeckは effective capability metadata により該当操作だけ unavailable 表示できる。

---

## 16. Security

- add-on serviceは非root
- loopback接続を基本
- raw session cookieを渡さない
- short-lived audience-bound credential
- capability-scoped API
- manifest fail closed
- embedded view sandbox
- CSP/frame policyを明示
- secretsをmanifest/url/logへ含めない
- contribution schema/body size limit
- health/schema endpointのSSRF境界をloopback/approved endpointへ制約

---

## 17. Migration plan

### Phase A

- v1維持
- v2 schema / registry
- generic contribution metadata
- enabled-only sidebar/commands/settings

### Phase B

- sandboxed embedded view
- design token / browser bridge

### Phase C

- scoped service credential
- Files/Project bridge
- notifications/audit

### Phase D

- remote Jobs adapter
- AI Resource Broker lease

### Phase E

- remote Workflow executor
- remote Agent tools

### Phase F

- generic context actions for Project Lab / Files / GitHub / future surfaces

---

## 18. Acceptance criteria

- v1 plugin tests remain passing.
- v2 add-on can install without adding its implementation code to ControlDeck repository.
- installed/disabled add-on contributes nothing executable or visible.
- enabled add-on navigation/commands/views appear without ControlDeck rebuild.
- disable removes effective contributions and direct disabled route is rejected/unavailable.
- addon service crash does not crash ControlDeck.
- addon cannot access raw ControlDeck session cookie or unrestricted APIs.
- plugin file grants cannot escape allowed roots.
- historical workflow nodes remain parseable when plugin is absent.
- same generic host mechanisms can integrate a non-Media add-on without adding a plugin-specific code path.

---

## 19. Architecture rule

**If ControlDeck must learn the domain model of a particular add-on, first ask whether the missing concept is actually a generic host capability.**

Generic host capabilities belong in ControlDeck. Domain-specific behavior belongs in the add-on.
