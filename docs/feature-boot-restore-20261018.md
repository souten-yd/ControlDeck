# 再起動後の Media Forge / SonicForge 復旧 — 2026-10-18

## 原因

実Hostの導入状態は両方とも `installed=true`, `managed=true`,
`requested_enabled=true` だが、`enabled=false`, `health=error`,
`error="service is stopped"` だった。Audioメニューの実体はSonicForge。

`current` とbundle、永続データは存在していた。実unitは
`cdapp-feature-media-forge.service` / `cdapp-feature-sonic-forge.service`。
両方 `inactive/dead`, `UnitFileState=disabled` で、そのbootの起動記録もなかった。
Hostの `control-deck-web.service` はenabled、データmountも正常。

bundle導入処理はunitを書いてその場で起動するが、自動起動登録もHost起動時の復旧もなかった。
さらにFeature有効化はhealthを先に検査し、正常なbundleでもサービス停止時に
「正常な機能を先に導入してください」と拒否していた。今回確認した障害はファイル破損ではない。

## 修正

- Host起動時、worker threadで有効な導入済みrelease-bundleを一度確認・起動する。
  Hostの既存mount待機後に実行し、Add-onの保存済みenabledを正とする。
- 停止・失敗・unit欠落の場合は導入済みmanifest/entrypointを検証してunitを復元し、
  起動後healthを確認する。稼働中のサービスは再起動しない。
- 無効化したAdd-onを有効に戻さず、権限再付与・download・provision・再導入を行わない。
  1件の起動失敗はログに残し、他のAdd-onの復旧を継続する。
- 通常の有効化にも同じ復旧処理を適用。設定画面は導入済みで復旧が必要な状態を表示し、
  「起動して復旧」から操作できる。

Add-on unit自身の `UnitFileState=disabled` は意図どおり。
起動の所有者を、自動起動設定済みのHostに統一している。

## 実機での確認

実行中Host Jobが0の時点で通常の `./deck.sh` を実行。
Host PIDは160578→245907。両Add-onを手動起動せず、Host起動処理によって
Media Forge PID246038 / SonicForge PID246101が起動した。
23:11:50に両featureの起動確認ログを記録。3unitともactive、NRestarts=0。
復旧処理をもう一度呼んでもAdd-onのPIDは変わらなかった。

| 対象 | 復旧前後で一致した値 |
| --- | --- |
| Media Forge | version 0.33.24、jobs 1684、assets 1643 |
| SonicForge | version 0.6.37、jobs 833、assets 613、voices 27、meeting_segments 12 |

`current` の解決先、上記DBの件数、既存backup/旧DBの記録件数を比較して一致。
両featureはhealthy。SonicForgeの環境5項目
（core / speech-essentials / gpt-sovits / game-audio / music）はすべてok。

- backend全体: **1195 passed, 2 skipped, 1 warning / 105.74秒**。
  新規11ケースで停止・unit欠落・失敗状態からの復旧、無効化優先、権限保持、
  path検証、失敗分離、二重起動防止を検証。
- frontend production build成功。
- 実Chrome E2E: **4 passed / 19.1秒**。1280px/320pxの両方で設定の利用可能表示、
  iframe bridge ready、Media Library画像読込、Sonic Libraryの実再生進行を確認。
  外側ページの横overflowなし、pageerrorなし。
- 音声の個別確認でもHTTP206/audio-wav、1.65秒のクリップのloadedmetadata→playing→
  timeupdate→endedを確認。新規生成はしていない。
- 検証用sessionは終了後に取消し、資格情報ファイルを削除。既存accountは変更していない。

初期の音声E2Eは、隠れた未初期化playerの選択と、初回Library取得の重複応答による
DOM置換で失敗した。実際のPlay操作と再生進行を検証し、該当assets取得の完了を待つ形に修正。
長時間接続があるためページ全体のnetworkidle待ちは使わない。最終4ケースで通過。
テスト中のブラウザ切断によるHostのClientDisconnect記録はあり、全ログ無エラーとはしない。

## 残る検証境界

- **OS/PC自体の再起動はNOT TESTED**。実サービスが停止した状態からのHost再起動と
  自動復旧を確認した。物理スマートフォンもNOT TESTED。
- 新規画像・音声生成、GPU推論中の再起動、生成品質はNOT TESTED。
- 導入済みSonicForge 0.6.37はLibraryの直接URL `/library` にHTTP404を返す。
  workspaceルートからLibraryボタンで開く・再生する操作は成功。
  Library URLの直接読込/再読込の問題は今回のサービス起動修正には含めていない。

ローカル証跡（秘密値なし、失敗した検証ログも保持）:
`/data1tb/ControlDeck/data/maintenance/feature-boot-restore-20261018/`。
`controldeck-feature-boot-before.json` / `after.json` / `services-after.txt`、
`controldeck-feature-boot-tests.log` / `controldeck-feature-boot-build.log` /
`controldeck-feature-boot-deploy.log` / `controldeck-feature-boot-browser-scoped.log`。
