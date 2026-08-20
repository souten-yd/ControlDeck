# Control Deck 宣言型拡張機能 SDK

## 目的と境界

プラグインは、Control Deck と別プロセスで稼働する Web アプリをナビゲーションへ安全に公開する仕組みです。
本体プロセスはプラグインの Python / JavaScript を import せず、コマンドも実行しません。外部アプリの起動、停止、
認証、更新は「Apps」またはそのアプリ自身で管理します。プラグインを無効化・削除しても外部アプリ本体には触れません。

SDK v1 の capability は `navigation` です。manifest の未知フィールドや未知 capability は fail closed で拒否します。

高度な別プロセスservice向けの Add-on v2 は、v1を破壊せず別contractとして追加します。v2のregistry／UI／bridgeは
段階実装中であり、PR-0時点で利用できるのはmanifest contract、lint、fake service harnessです。設計の正本は
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
- v2 install/enable/effective registryはPR-Aまで未提供。lint成功を利用可能性と誤認しない
