# llama.cpp 複数GGUF catalog / instance 詳細設計

最終更新: 2026-07-15

## 再監査結果

現状は`llama-runtime.json`の`instance` 1件だけを保持し、固定
`cdapp-llama.service`を生成する。共通provider APIもこの1件だけを返すため、別GGUFを
登録すると前のモデル設定が上書きされる。「モデルごとのCTX/KV/MTP/MoE設定」と
複数モデルの同時ロード要件を満たしていない。

## 保存形式と移行

- server側`llama-runtime.json`に`instances: {alias: config}`と`selected_alias`を追加する。
- 旧`instance`は互換API用mirrorとして維持する。初回読込時、旧model_pathがあればaliasを
  正規化して`instances`へ無損失移行する。新しい既定キーは各instanceへ補完する。
- aliasは`[A-Za-z0-9._:-]`、1〜128文字、最大8件。portは1024〜65535でcatalog内一意。
- pathは既存通り`Path.resolve()`とfiles許可root、`.gguf`通常ファイルを検証する。
- catalog削除は設定とunitだけを削除し、GGUF本体は削除しない。ファイル削除はFiles機能で明示操作する。

## 実行モデル

- 1 instance = 1 `llama-server` = 1 systemd user unit = 1 localhost port。
- unit名はaliasの安全な短縮文字列+SHA256短縮値から生成し、衝突・unit注入を防ぐ。
- hostは常に127.0.0.1。自由引数は禁止し、既存の型付き設定だけをargv配列へ変換する。
- 各unitの`ExecStartPre`に同じAMD GPU profile helperを置く。手動load時もpreflightを先に適用する。
- 複数unitは同時起動可能。ただしVRAM不足はllama-server/systemdの起動失敗として個別表示し、
  他instanceやOllamaを停止するかは共通coexistence policyに従う。
- `auto_start`が有効なinstanceだけunitをenableする。無効時はstartのみでboot自動起動しない。

## 共通provider契約

- llama.cpp adapterの`list`は全catalog modelを返し、alias単位でload/unload/configure/deleteする。
- model detailsへpath、port、base_url、backend、unit、loadedを返す。
- 共通healthはruntime導入状態とinstanceごとのsystemd/HTTP healthを返す。
- providerのload/unloadはaliasを必須とし、選択中1件へ暗黙に書き換えない。
- 旧`/llama/instance`、`/llama/start`、`/llama/stop`はselected_aliasへの互換routeとして維持する。

## idle / 共通設定

- 共通`RuntimePolicy.idle_unload_enabled/minutes/max_loaded_models`を全runtimeの上位設定とする。
- instanceに`idle_exclude`を持たせ、trueなら共通idle停止から除外する。
- Control Deck経由の生成直前にbase_urlからinstanceを特定して`last_used_at`を更新する。
  直接localhost endpointを利用した外部clientは追跡できないため、idle停止の対象外保証が必要なら
  `idle_exclude`を使用する。この制約をUIへ明記する。

## UI/UX

- llama.cpp panel上部にcatalog selectorと「GGUF設定を追加」。選択モデルの詳細設定だけを表示する。
- 一覧で稼働/停止、port、backend、pathを識別し、保存、保存して起動、停止、設定削除を提供する。
- alias/port重複、同一path重複、最大件数は保存前とAPI双方で明確なエラーにする。
- 共通設定はruntime selector直下、CTX/KV/MTP/MoE等は各catalog model内に置き、重複表示しない。

## 受入条件

- 旧単一JSON移行、2件保存、alias/port一意、unit名、unit内容、list/load/unload/deleteを自動テスト。
- 2つの小型GGUFがある場合は同時healthを確認する。実機に大型GGUF 1件だけの場合は、既存モデルの
  新alias unitで起動・応答を確認し、2件目はunit生成とport分離まで非破壊確認する。
- backend全テスト、本番build、実サービスAPI、1280px/320pxのcatalog操作を確認する。
