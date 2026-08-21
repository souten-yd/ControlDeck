# Control Deck 宣言型拡張機能 SDK

## 目的と境界

プラグインは、Control Deck と別プロセスで稼働する Web アプリをナビゲーションへ安全に公開する仕組みです。
本体プロセスはプラグインの Python / JavaScript を import せず、コマンドも実行しません。外部アプリの起動、停止、
認証、更新は「Apps」またはそのアプリ自身で管理します。プラグインを無効化・削除しても外部アプリ本体には触れません。

SDK v1 の capability は `navigation` です。manifest の未知フィールドや未知 capability は fail closed で拒否します。

高度な別プロセスservice向けの Add-on v2 は、v1を破壊せず別contractとして追加します。v2はregistry、host UI、
opaque embedded view／Bridge、Resource Broker、remote Workflow／Agent／Context executionまで実装済みです。設計の正本は
[design-addon-platform-v2.md](design-addon-platform-v2.md)です。ユーザー向けUIではv1/v2とも「拡張機能」と表示します。

## manifest

`control-deck-plugin.json` を UTF-8、64 KiB 以下、実行ユーザー所有かつ other 書込み不可で作成します。
Control Deck 管理領域へ保存したコピーは group / other 書込み不可に固定されます。

```json
{
  "api_version": "1",
  "id": "example-gui",
  "name": "Example GUI",
  "version": "1.0.0",
  "description": "Independent local web application",
  "publisher": "Your name",
  "capabilities": ["navigation"],
  "navigation": {
    "label": "Example",
    "url": "http://127.0.0.1:9010/",
    "permission": "apps.view"
  }
}
```

- `id`: 英小文字開始の英小文字・数字・`-`、最大64文字
- `version`: SemVer形式
- `navigation.url`: `/` 開始の同一 origin path、HTTPS URL、または loopback HTTP URL。認証情報と fragmentは禁止
- `navigation.permission`: Control Deck に存在する権限。これはリンクの表示制御であり、外部アプリ側の認証を代替しない

## CLI

```bash
chmod 600 control-deck-plugin.json
./deck.sh ext lint ./control-deck-plugin.json
./deck.sh plugin validate ./control-deck-plugin.json
./deck.sh plugin install ./control-deck-plugin.json
./deck.sh plugin enable example-gui
./deck.sh plugin list
./deck.sh plugin disable example-gui
./deck.sh plugin uninstall example-gui
```

リポジトリ内の `examples/plugins/example-gui/control-deck-plugin.json` も雛形として利用できます。

`ext lint` はv1/v2をversion dispatchし、未知の実行contract／capability／危険なURLを拒否します。
v2の未知presentational fieldは無視した項目をwarningとして返します。v2の実例と起動可能なharnessは
`tools/fake-addon/control-deck-addon.json` / `tools/fake-addon/run.py` を参照してください。

設定画面の「GUIプラグイン」から JSON を登録することもできます。登録・有効化・無効化・削除は
`settings.manage` 権限と CSRF 防御を必須とし、すべて監査ログへ記録されます。manifest は
`~/.local/share/control-deck/plugins/<id>/` に mode 0600 で原子的に保存されます。

## 配布側チェックリスト

1. Web アプリを非 root で起動し、必要なら Apps の user systemd 管理へ登録する
2. 外部公開する場合は HTTPS とアプリ固有の認証・権限検査を実装する
3. URL、ログ、manifestへ token、password、cookieなどの秘密値を入れない
4. 320px と PC 幅の両方で GUI を確認する
5. API v1 で未定義の capability を先取りして記述しない

## Add-on v2 contract（PR-0）

- contract versions: Add-on `2.0`、Bridge / Theme / Health `1.0`
- `requires.addon_contract` は `>=2.0 <3.0` 形式。major非互換はhostが拒否する
- runtimeは別プロセスの`external-service`。HTTPSまたはloopback HTTPのみ
- contribution typeはnavigation、embedded view、command、quick action、settings、workflow executor、
  agent tool、context action、setup checklistの固定集合
- host capabilityはallowlist。任意Python/JavaScriptをControl Deckへimportしない
- v2のlint成功だけを利用可能状態とは扱わない。PR-Aのregistryへinstallし、enable、health、grantを通った
  contributionだけがeffectiveになる

## Add-on v2 registry（PR-A）

管理APIはすべて認証必須です。install/enable/disable/recheck/uninstallと詳細・activityは`settings.manage`、
effective registryとrevision SSEはログイン中ユーザーへ、そのユーザーのpermissionでfilterして返します。

```text
GET    /api/v1/addons
POST   /api/v1/addons
GET    /api/v1/addons/{id}
POST   /api/v1/addons/{id}/enable
POST   /api/v1/addons/{id}/disable
POST   /api/v1/addons/{id}/recheck
DELETE /api/v1/addons/{id}
GET    /api/v1/addons/{id}/activity
GET    /api/v1/addons/effective
GET    /api/v1/addons/effective/events
```

install直後はdisabledです。enableは要求capabilityのgrantと即時health確認を行います。healthは15秒間隔、
失敗時最大120秒のbackoff、3回失敗でunavailable、degradedからの復帰は2回成功です。enabled navigationは
healthで消えませんが、unavailableな実行contributionはeffective registryから外れます。effective応答は
user permission別ETagを返し、SSEは本文を含めずrevision/ETagだけ通知します。

managed manifestは`data_dir/addons/<id>/control-deck-addon.json`へ0600で原子的に保存します。
壊れた／将来majorのmanifestは消さず`incompatible`として表示し、disable/uninstallできる一方でenable/effectiveを拒否します。
非loopback runtimeは`addons.allowed_origins`へpathなしHTTPS originを明示した場合だけhealth接続できます。

## Add-on v2 execution contributions（PR-E）

実行対象はmanifestに存在するだけでは足りません。Add-onがenabled、対象contributionがavailable、呼出し利用者が
manifestの`permission`を持つ場合だけdiscovery／invokeできます。実行中にdisableされた場合も送信直前の再検査で停止します。

```text
GET  /api/v1/addons/execution-contributions
POST /api/v1/addons/{id}/agent-tools/{contribution_id}/invoke
POST /api/v1/addons/{id}/context-actions/{contribution_id}/invoke
GET  /api/v1/workflows/node-catalog
```

schema endpointは5秒／64KiB、実行endpointは120秒、request 1MiB／response 4MiBが上限です。redirectは追従せず、
schemaとresponseはJSON objectだけを受理します。upstreamへControlDeck session Cookie、利用者Authorization、CSRF tokenは
転送されず、Add-on audienceへ束縛した短命service tokenだけが送られます。

### Workflow executor

`workflow_executors`はhostで`addon.workflow:{addon_id}:{contribution_id}` nodeになります。input/output schemaは
JSON Schema Draft 2020-12 object schemaにしてください。hostはtemplate解決後inputとupstream outputを検証します。
dry-runはschema検証だけでendpointを呼びません。disable／unavailable時も保存済みnodeとedgeは削除されず、Editorが
必要なAdd-onを表示し、publishと実行をfail closedにします。

### Agent tool

`agent_tools`は実行Workflow ownerの現在permissionでLLM tool listへ追加されます。各callはControlDeck Jobに紐づき、
同期応答は`job_id`、`asset_id`（`job-result:{job_id}`）、bounded outputです。`wait: false`では202とJob IDを返します。
raw host pathは引数schemaが許可しても拒否されます。ファイルはhostが発行した`asset:`／`grant:` IDで設計してください。

### Context Action

`context_actions.contexts`は`file`、`project`、`workflow`、`job`の固定集合です。Files／Project Labのhost UIから実行でき、
hostが対象の存在・realpath containment・Job ownerを検証します。file pathはupstreamへ送られず、`grant:` IDへ変換されます。
payloadは`input`と`context: {type, resource_id, grant_id}`だけです。`grant_id`は要求単位のscoped tokenであり、
ControlDeck全体またはproject rootへの包括権限ではありません。

## Add-on Runtime Host API

Browser Bridgeはtheme、route、picker、notification等の軽量UI操作だけを担います。GPU、Job、file contentは
Add-on serviceが次のservice-to-host APIを使用し、browser JavaScriptからleaseを取得してはいけません。

```text
Authorization: Bearer <ControlDeck service token>
X-Control-Deck-Addon-ID: <addon_id>

POST   /api/v1/addon-runtime/token/introspect
POST   /api/v1/addon-runtime/{addon_id}/jobs
PATCH  /api/v1/addon-runtime/{addon_id}/jobs/{host_job_id}
GET    /api/v1/addon-runtime/{addon_id}/jobs/{host_job_id}/control
POST   /api/v1/addon-runtime/{addon_id}/resources/requests
GET    /api/v1/addon-runtime/{addon_id}/resources/requests/{request_id}
DELETE /api/v1/addon-runtime/{addon_id}/resources/requests/{request_id}
POST   /api/v1/addon-runtime/{addon_id}/resources/leases/{lease_id}/{activate|renew|release}
POST   /api/v1/addon-runtime/{addon_id}/resources/leases/{lease_id}/credential/refresh
GET    /api/v1/addon-runtime/{addon_id}/grants/{grant_id}
GET    /api/v1/addon-runtime/{addon_id}/grants/{grant_id}/content
POST   /api/v1/addon-runtime/{addon_id}/files/outputs
PUT    /api/v1/addon-runtime/{addon_id}/files/outputs/{output_id}/content
POST   /api/v1/addon-runtime/{addon_id}/files/outputs/{output_id}/commit
```

Runtime APIはsession cookie、CSRF、`settings.manage`をservice identityとして受理しません。token署名、`kind=service`、
10分以内の期限、`aud`・header・pathのAdd-on ID一致、installed／enabled、操作別capability grantをHostが検証します。
HMAC keyをAdd-onへ共有しません。serviceがHostから受け取ったrequest tokenを検証するときはintrospectionへそのまま送り、
`active`、`addon_id`、`subject`、`expires_at`、`granted_capabilities`を確認してください。

`sub=job:{id}`では`POST /jobs`が既存Host Jobへattachし、新しいJobを作りません。数値user subjectではAdd-on UI起点として
Host Jobを作成します。Resource requestの`job_id`はこのHost Jobに一致させます。service schemaに`owner`はなく、Hostが
`addon:{addon_id}`を強制します。priority上限はinteractive 30、agent-interactive 25、workflow 15、background／batch 0、
maintenance -10です。同一Add-on/userのactive外部Jobは8件までです。Job PATCHはphase必須、progress単調増加、
通常update 2Hz以下、terminal result 16KiB以下です。

disable後は新規resource requestとrenewが409、waiting requestはcanceled、関連Jobはcanceledになります。active leaseはworkerが
停止するまで予約を維持します。serviceはcontrolをpollし、worker停止後にreleaseしてください。応答しない場合はlease TTLが
fail-safeとして解放します。無効化後もcontrol参照、waiting cancel、active lease releaseだけは有効期限内tokenで許可されます。

Host pickerが返す`grant:`はuser／addon／kind／期限に束縛されます。metadata/content/output APIはhost pathを返しません。
readは選択時のrealpath／inode／size／mtimeを再検証し、outputはexport grant配下のprivate stagingへ宣言size以内でuploadしてから
commitします。raw `/share/...`や`/home/...`をAdd-on protocol、Job result、asset metadataへ入れないでください。

実装例と実E2Eは`tools/fake-addon/`と`frontend/e2e/addon-v2-execution.spec.ts`を参照してください。
