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
