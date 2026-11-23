# 作業場から実機（Android・ESP32/M5Stack・Raspberry Pi）を開発する設計

状態: 2026-11-23 設計と第 1 段の実装。
利用者の要望（2026-11-23）: 「USB につながってる装置（Android 携帯・M5Stack・ラズパイ）を OpenCode から開発できないか。開発環境は作業場の中に構築してよい。USB での書き込みと動作確認、Wi-Fi での動作確認もできるとよい。危険なら USB のみ、または作業場（プロジェクト）を限定して許可する設定でもよい。安全に」。

## 1. 結論（推奨の方式）

- **ツールチェーン（esptool・PlatformIO・Arduino CLI・Android SDK/adb など）は作業場の中に入れる**（利用者の許可済み）。ホストには入れない。
- **機器は作業場ごとの許可制。既定は全部切**。利用者（管理者）が「この作業場に渡す機器」を 1 台ずつ選ぶ。AI（OpenCode）は許可を広げられない。
- **USB が先、Wi-Fi は後**。USB はホストの箱の起動引数（`--device`）で渡す具体的な機器 1 台に閉じる。Wi-Fi は LAN への横移動の危険があるので、相手の IP:ポートを 1 つずつ許可し、ほかの LAN には出さない仕組みを作ってから（第 3 段）。
- adb は **箱の中の adb サーバー**に USB を渡す（ホストの adb サーバーへはつながない）。ホストの adb サーバーは全機器を見えるため、機器の限定にならない。

## 2. 調べたこと（公式 → GitHub issue・フォーラム）

- **rootless podman への USB・シリアル**: `--device /dev/ttyUSB0`（`:rw`）で渡せる。ホスト側の権限（dialout・plugdev）が要り、rootless では `--group-add keep-groups`（crun のみ）でホストの補助グループを引き継ぐ。`--userns=keep-id` と併用できる。keep-groups でも拒否される例があり、その場合は udev の規則で機器の所有者を利用者にする（Home Assistant のフォーラムの報告）。機器が `nobody` 所有に見える問題は podman の issue に既知。出典: Red Hat「supplemental groups podman containers」「files devices podman」、containers/podman の issue 10166・18845、joachimburket.github.io の記事。
- **抜き差しで番号が変わる**: ttyUSB0/1 は順序で決まる。`/dev/serial/by-id/usb-<ベンダー>_<製品>_<シリアル>-ifNN-portN` は機器に固有で変わらない。本実装は by-id の名前を保存し、箱の起動のたびに実体へ解決する。USB の `/dev/bus/usb/BBB/DDD` は抜き差しのたびに DDD が変わるので、adb は「ベンダー:製品:シリアル」を保存して起動時に sysfs から引く。
- **adb**: 箱に `/dev/bus/usb/BBB/DDD` を渡せば箱の中の adb サーバーが使える（docker-adb、DEV の rootless podman の記事）。ホストで adb サーバーが先に掴むと箱から見えない（ホストの `adb kill-server` が要る）。udev の規則（plugdev、android-udev-rules）はホストで設定し、箱の中の udev は動かない。機器を再接続すると DDD が変わり、箱へは再起動まで反映されない（箱を起こし直す）。
- **Wi-Fi の adb**: Android 11 以降の「ワイヤレスデバッグ」はペアリング（6 桁コード）→接続。機器側で承認する鍵（RSA）を箱の中に置くので、鍵は作業場ごとに分ける。公式資料を今回の検索では取れなかったため、第 3 段の前に Android 公式（developer.android.com の adb ページ）で再確認する。
- **ESP32/M5Stack**: esptool・PlatformIO・Arduino CLI はシリアル（USB-UART）だけで書き込める。ネットワークは不要（ライブラリ・ボードの取得は外部への取得なので、利用者の了承の取り方を別に決める。現状の箱は npm・pip の外部取得を禁止している）。
- **Raspberry Pi**: SSH（Wi-Fi・有線）での配備が主。USB ガジェットモード（RPi の USB を Ethernet・シリアルとして見せる）は、ホストに仮想の NIC やシリアルが増えるだけで、シリアルとして渡せば第 1 段と同じ扱い。SSH は Wi-Fi の方式で第 3 段。

## 3. 危険と緩和

| 危険 | 緩和 |
|---|---|
| 渡した機器には任意のコマンド・書き込み・消去が届く（adb shell は端末内の任意のコマンド、シリアルは書き込み・設定の消去） | 既定は渡さない。1 台ずつ選ぶ。画面に危険を明記。書き込み・消去は承認制（第 2 段） |
| 機器をホストの別の機器と取り違える（ttyUSB の番号の入れ替わり） | by-id・ベンダー:製品:シリアルで固定し、起動のたびに解決。見つからなければ渡さず警告 |
| 箱の AI が許可を広げる | 許可は箱の外（`meta`、箱から書けない場所）に保存し、読み込み時に検査。変更 API は管理者権限（settings.manage）で、箱の中継は通さない。メタに不正な値があれば「改ざんの恐れ」で箱を動かさない |
| `/dev` の任意のパスを渡される（`/dev/sda` など） | 形を厳しく限定: シリアルは by-id の名前（`/` と `..` を含まない）で実体が `ttyUSB*`・`ttyACM*`、adb は `/dev/bus/usb/BBB/DDD` だけ。一覧にある機器だけ選べる |
| 機器からホストへの攻撃面（USB の悪意ある機器・Android 端末の脆弱性） | 渡すのは 1 台だけ・ホストの adb サーバーに触れさせない・箱は rootless・ほかのホストの USB は見えない |
| Wi-Fi: LAN への横移動 | 第 3 段まで渡さない。許可は相手の IP（私的アドレス）とポート 1 つずつ。ほかの LAN には出さない（中継で絞る） |
| 実行の追跡ができない | 渡す・外すを監査（`opencode.workspace.device_attach` / `device_detach`、機器の種類と ID） |

## 4. 作業場ごとの許可

`meta["devices"] = [{"kind": "serial", "id": <by-id の名前>}, {"kind": "adb", "id": "<vid>:<pid>:<serial>"}]`（既定は空）。
- 一覧 API `GET /opencode/devices`（settings.manage）: by-id と sysfs を読むだけ。画面に出すのは名前・ベンダー・シリアルの末尾 4 文字と実体のパス。adb はインターフェースがクラス ff・サブクラス 42・プロトコル 01 で、シリアルを持つ機器だけ。
- 設定 API `PUT /opencode/workspaces/{name}/devices`（settings.manage・持ち主の作業場のみ）: 全部置き換え。今つながっている機器だけ選べる。
- 反映: 箱のコンテナの起動（`podman_argv`）ごとに解決して `--device 実体:実体:rw` と `--group-add keep-groups` を足す。すでに動いているコンテナには効かない（次の起動から）。
- 画面: 作業場の結果のカードの「USB 機器」。管理者にだけ表示。

## 5. 機器の操作の前の承認（第 2 段）

| 操作 | 既定 |
|---|---|
| 読み取り（デバイス一覧・ログ・`adb logcat`・`esptool chip_id`・シリアルモニタ） | 承認なし |
| 書き込み（`esptool write_flash`・`adb install`・`adb push`） | 承認（利用者が会話で許可） |
| 消去（`erase_flash`・`adb shell rm`・`pm clear`・`adb uninstall`・ファクトリーリセット） | 毎回承認 |
| 任意のシェル（`adb shell`） | 承認 |
実装は OpenCode の権限（permission の ask）を作業場の設定から生成して強制する。AI が自分の設定を書き換えても反映されない場所（箱の外の設定）から生成する。

## 6. 段階の計画

1. **第 1 段（実装済み）**: USB のシリアルと adb の USB。一覧 API・作業場ごとの選択・起動引数・画面・監査・試験。
2. **第 2 段**: 承認（§5）。ツールチェーンの作業場内の導入手順（PlatformIO・esptool・Arduino CLI・Android SDK を作業場のホームに入れる。取得の了承の方式）。箱の中の adb サーバーの起動と、機器の再接続（DDD の変化）の扱い。実機のシリアル通信・adb の通しの検証。
3. **第 3 段**: Wi-Fi。許可は IP（私的アドレス）とポート 1 つずつ。箱の pod の外向きの通信を、許可した宛先だけに絞る（中継の拡張）。SSH（Raspberry Pi）と adb の TCP。鍵は作業場ごと。
4. **第 4 段**: Raspberry Pi の USB ガジェットモード・SWD/JTAG（debug プローブ）。udev の規則のセットアップの案内を「機能と拡張」へ。

## 7. 既知の制限（第 1 段）

- 箱のコンテナは機器を持ったまま動くので、機器を外す・差し替えるには箱を起こし直す。
- ホストの利用者が dialout・plugdev に入っていない、udev で権限が無い機器は箱から開けない（keep-groups が効かないとき）。実機の確認は Opus が行う。
- ツールチェーンはまだ箱に入っていない（第 2 段）。
