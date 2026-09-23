# OpenCode endpoint監視の同期待機を除去

## 原因と境界

2026-09-23 18:41:25 JSTのstall stackは`llama.idle_unload_loop` →
`_revive_endpoint_for_opencode` → `registry.opencode_enabled/is_enabled/status` →
`subprocess.run` → `wait`だった。18:41:26に30秒watchdog、18:41:33にSIGABRT、
18:41:56にHostが自動復帰した。PID486604→604536、memory peak430.5MiBで、
この記録の再起動理由はOOMではなくwatchdogである。

Add-onの生成中に接続が切れたが、待機していた処理は汎用Hostの定期監視。
Add-on側でこのevent loop待機は修正できないため、ControlDeckの独立sliceで扱う。
モデル、Add-on固有route、認証、OpenCodeの継続/停止方針を変更しない。

## 修正

Gateway利用時は設定確認後すぐ終了し、不要なfeatureの`--version`を実行しない。
直結時は従来の有効化/attached session/稼働endpoint確認を同期helperへまとめて
`asyncio.to_thread`で実行し、必要な場合だけ既存の非同期起動を呼ぶ。
同じidle監視のpolicy読込、instance一覧、利用時刻保存もthreadで行う。
detach/無効feature/既起動への起動条件は維持し、キャンセル時はモデルを起動しない。

## Source実測

設定/feature/session/instanceの各遅延でloop heartbeatが継続する回帰検証を追加。
関連38tests/9.00秒/1warning通過。
隔離configの実uvicornへHTTPを送り、実際の`registry.status`がprivateな
4秒待つversion実行ファイルを2回照会している間にも81回のhealth応答を測定。
全体8.072229秒、最大応答0.001253秒、起動判定結果は直結port1件。
実モデル起動はstubでありLLM受入ではない。Gatewayではprobe0を別途確認。
private証跡はMediaForge feature maintenanceの`release-0.33.20-20260923/host-revive-source/`。

初回全体testは1173pass/2fail/2skip/119.29秒。
失敗はresource Jobの1秒待機と、新規worktreeにfrontend/distがなく
既存project routeの405期待が404になる検査。製品条件やassertionは緩めず、
配布済みfrontendを参照して失敗2件を再検査、2pass/2.12秒。
2回目の全体testは1174pass/1fail/2skip/120.89秒。
残るresource検査は単体実行で通るが同file全体でも再現。各testが終端statusを見た直後に
event loopを閉じ、未完のDB保存とrunner slot解放を取り消して次testへslot数を漏らしていた。
test helperで同じloopの終端Job taskを最後までawaitするように修正。
assertion/待機秒/製品のJob動作は変更せず、対象fileと監視回帰23tests/1.82秒を通過。
失敗済みの旧単独pytestプロセスは自身のPID/cwd/引数を照合してSIGINTで終了した。
最終`./deck.sh test`は1175passed/2skipped/1warning/105.71秒、exit0。
installed受入はマージ後に行う。以後製品code変更なし。
