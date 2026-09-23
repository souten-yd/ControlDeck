# Host再起動後も継続するOpenCodeの状態確認と明示停止

## 方針

利用者はOpenCodeの継続実行を許容し、Codex等からの停止も求めた。
Host終了に連動する自動停止案は取り下げ、providerの起動/終了処理は変更しない。
修正対象はHost Jobがinterruptedでも独立unitが動く場合の表示と、権限内の個別停止。
利用者の停止指示とエージェント自身の検証cleanupは許容する。別の実行を一括停止しない。
この状態はHost Jobs/systemdの責務であり、Add-on側からは解決できないため汎用Hostで対応。

## 変更

- 永続opencode.run/interruptedだけ、保存Job IDから固定cdfeature-opencode unitを照合する。
  通常Jobs一覧/詳細/streamで生存中ならrunning、確認不能なら状態不明と表示する。
  観測だけでunitやDBの過去記録は変更しない。終了後の失われたpipe出力は推測復元しない。
- 既存owner/RBAC付きcancelで当該unitだけを停止。停止を確認してからcanceledと監査を保存。
  不明/停止未確認は503、所有者外は404、権限不足は403。停止済みへの再操作は409。
- OpenCode画面の「バックグラウンドの実行」から最近100件を表示・個別停止できる。
  開いた時だけ通常Jobs APIを再取得し、取得失敗は空履歴や停止済みに変換しない。
  対話TUI/tmuxの接続と停止は従来どおり。provider/run_chat/workflowの寿命は変更なし。

## source実測

証跡root: MediaForge managed data `maintenance/shopping-street-20260923`。

- `./deck.sh test`: **1171 passed / 2 skipped / 1 warning、107.27秒、exit0**。
  `opencode-recovery-full.log`。関連32件の後、read-only role拒否/query timeoutの回収も追加。
  最終関連23件（新規21件＋既存stream cleanup2件）通過。
  最初のtest fixture import/JobControl.kind不足はfixture修正後に全gateを通した。
- `npm run build`: TypeScriptとVite完了、24.60秒（bundle-size warning）。
  `opencode-recovery-build.log`。稼働distへのsymlinkを外し候補worktree内だけでbuild。
- `opencode-recovery-source-4/probe.py run`: 実systemd/HTTP/ブラウザ、隔離DBと通常fixture認証。
  3つのheartbeat unitがsource Host再起動を越えて継続し、Jobsがrunning/external_running。
  PC1280px/320pxの停止ボタンで2つを個別停止。毎回control unitはactiveのまま。
  最後はCodex同等の認証付き通常HTTP cancelでcontrolだけを停止。DB全3件canceled。
  横overflow0、停止ボタン44px、page error0、own unit cleanup後残存0。
  `report.json`/`browser.json`/`browser-{1280,320}.png`。これは実OpenCode推論の証拠ではない。
- 初回fixtureは再起動時にtest userを重複作成、2回目は隔離Feature未有効でUIへ到達不可。
  3回目はAPI/UI停止自体は成功したがfixture Host cleanupの20秒待ちがtimeout。
  所有fixture Hostだけを明示停止し、4回目はfixture Uvicornのgraceful期限5秒で全体通過。
  製品のshutdown期限/常駐処理は変更していない。各失敗証跡は保持。
- 新モデル取得0。稼働Host/既存LLM設定/MediaForge単視点runtime変更0。

NOT TESTED（このsource時点）: installed通常画面/実OpenCodeの再起動越し継続、
失われたstdoutの回収、物理携帯、chat.completion/workflowの同様の照合。
既存の実行中メモリJob/TUIの動作を、このsource fixtureで実OpenCode受入したとはしない。

## 31e3420 / PR340の通常導入と実OpenCode cold起動

active Host Job0、cdfeature/cdapp双方の全状態unit0、active lease/waiting request0を確認。
稼働DBの復旧用backup後、root mainへff-onlyして通常`./deck.sh`でfrontend build/起動。
PID394222→456446、health ok。llama-runtime.jsonとPixal採用receiptのhash不変、追加モデル取得0。
`opencode-recovery-installed/{update.log,installed.json}`。

通常operator認証でOpenCode画面を開き、1280/320pxで実履歴25件・進行中の停止ボタン1個を確認。
横overflow/page error0。旧interrupted Jobe67c69a17071はunit終了済みのため
external_result_unavailableとなり、成功へ書き換えない。これは閲覧のみ（mutations0）。
`browser.json`/`installed-{1280,320}.png`。停止の実行確認は上記source fixtureの証跡と区別する。

停止中（loaded=false）のQwen3.8-27Bを通常OpenCode Job2291014cbcf0でcold起動。
実session ses_f32fcf23cffecM4vaj4Dk0iWTg、77.058秒/succeeded。
readツール2回だけでREADME.mdとacceptance-summary.mdを読み、要求した3行を回答。
shell/MCP/別agent/編集0、Git clean。実Broker3要求が全てgranted、予約は
30,979,147,560B/0B/0B、全3lease released、expired/retry0。
`cold-{result,independent}.json`/`cold-resource-samples.jsonl`。
これは実cold起動成功の証拠。lock前の要求到着時刻を採取していないため、
以前の「2.021286秒差の同時cold要求」と同じ条件だと推測しない。

## 同名の実行の識別（source追補）

installedでOpenCode implement等の同じタイトルが並ぶことを目視した。
開始時刻/実行IDを各行へ追加し、停止ボタンのaccessibility名と確認文にも同じIDを付ける。
同じタイトルの3実systemd unitを使い、1280/320pxでIDに対応した2個だけを停止、
controlは継続、最後に通常HTTPでcontrolを停止。overflow0/button44px/page error0。
`opencode-run-identification-source/{report,browser}.json`。frontend build22.08秒。
識別表示変更後の`./deck.sh test`も1171 passed / 2 skipped / 1 warning、106.87秒、exit0。

## e34c7ca / PR341を通常導入し実OpenCodeの明示停止を受入

Job/全状態OpenCode unit/leaseの空きを確認し、DB backup後に通常deck.shで更新。
PID456446→486604、health ok、llama/Pixal runtime設定hash不変。追加モデル取得0。
検証担当者自身の読み取り限定OpenCode Jobcfce488f01aa/Qwen3.8-27Bを通常APIで起動。
実unit cdfeature-opencode-cfce488f01aa.service / MainPID487050のactiveを確認した。
通常operator認証のOpenCode画面で、1280pxと320pxに同じ実行IDと開始時刻/停止ボタンを表示。
320pxのタップでそのIDの確認ダイアログから停止。通常Jobsはcanceled、unitはinactive/MainPID0、
active GPU lease0、商店街Git clean。横overflow0/button44px/page error0。
これはfixtureではなく実installed OpenCodeの明示停止である。別の実行は停止していない。
証跡opencode-identification-installed/{installed,stop,stop-browser}.json、before-stop-1280/320.png、after-stop-320.png。

残る範囲: 実OpenCode推論中のHost再起動を意図的に起こす試験、失われたstdoutの回収、
lock前のcold同時到着時刻、物理電話。再起動越しは隔離Host/実systemd fixtureの実測まで。
この追記は文書のみ、PR341後の製品code変更なし。
