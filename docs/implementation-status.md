# 実装状況

最終更新: 2026-07-21

## Phase 4 ファイル圧縮／安全展開・高度preview 完了（2026-07-21 12:40 JST）

- 許可root内の通常file／directoryをZIPまたはtar.gzへ圧縮し、ZIP／tar.gz／tgzを新規directoryへ展開するAPIとFilesメニューを追加した。source／destinationは既存のrealpath＋許可root／deny-root検証を必ず通し、圧縮先と展開先は既存pathを上書きしない。作成・展開はいずれも同じ親directoryの予測不能な一時pathへ完了させ、Linux `renameat2(RENAME_NOREPLACE)`で既存pathへ上書きせず原子的公開するため、失敗時に部分archive／部分展開を公開しない。成功時は形式、項目数、非圧縮byte数だけを監査する。
- 作成時はsymlinkと特殊fileを拒否し、各通常fileを`O_NOFOLLOW`で開いてinode／device／sizeを再照合するため、検査後の差し替えで許可root外を読み込まない。展開前に全memberを検査し、絶対path、`..`、backslash traversal、NUL、4KiB超path、重複／file-directory競合、symlink／hardlink／device／FIFOを拒否する。最大10万項目、`files.max_upload_size_gb`と同じ非圧縮size上限、16MiBを超える最大200倍の圧縮率、64MiB reserveを残せない空き容量を拒否し、宣言sizeと実読込sizeの不一致でも全体をrollbackする。shellや外部archive commandは使わない。
- 認証・`files.view`付き`/files/preview`を画像／PDF／音声／動画に限定し、`Content-Disposition: inline`、`nosniff`、private/no-store、SVG sandbox CSPを付与した。Starletteのsingle／multi Range配信を維持し、mediaのseekと大容量PDFを全量memory化しない。HTML等のunsafe inline型は415で拒否する。
- Files UIはPDF、audio、videoを拡張子別のnative viewerへ接続し、PCは右側preview panel、320pxを含むmobileはSafe Area対応bottom sheetで表示する。entry主操作は従来どおりtapで開き、圧縮／展開、download、edit等は3点menuへ置く。圧縮はZIP／tar.gzを選択でき、展開はarchive名から新規folderを提案し、危険memberと既存項目を上書きしない境界を画面に明示する。

検証: backend全435件、archive／media集中3件、frontend TypeScript／production build成功。ZIPとtar.gzの往復、Zip Slip、backslash／symlink／hardlink・特殊file境界、高圧縮率、原子的な既存path上書き拒否、部分出力なし、preview 206／Content-Range／inline／nosniff、HTML 415を自動確認した。実service PID `361454`、active、health 200で、一時許可rootに作成した3-entry ZIPを実APIで圧縮→展開し、同名再作成409・既存archive不変、内容一致、`bytes 3-6/10` Rangeを確認した。認証付きChromium 1件で320／1280pxのPDF／audio／video surface、ZIP展開提案、tar.gz圧縮request、横overflowなしを確認した。一時user／login session／audit／file fixtureは0件に清掃済み。

## Phase 2／3 アプリ別GPU／VRAM・許可コマンドHC 完了（2026-07-21 12:21 JST）

- 管理アプリのsystemd MainPIDと子processだけを対象に、Linux DRMの`/proc/<pid>/fdinfo`標準統計からGPU engine累積時間とresident VRAMを有界収集する。PIDは数値から組み立てた固定`/proc` pathだけを使い、1 process最大512 FD、1 fdinfo最大32KiB、`O_NOFOLLOW`で読む。外部GPU CLIをApp一覧取得ごとに起動せず、同じDRM clientの継承FDをprocess tree内で重複計上しない。App ID＋MainPIDでsample世代を分離し、0.25秒未満の近接再取得や再起動前counterとの誤差分を避ける。GPU clientがないアプリは0、driverが項目を公開しない場合はN/Aへ縮退し、アプリ全体をエラーにしない。
- `AppRuntime`へ`gpu_percent`／`vram_bytes`を追加し、PCカードではCPU／RAMと同じcompact metrics、詳細bottom sheetではモバイルを含めGPU／VRAMまたはN/Aを示す。App APIからはprocess名やfdinfo本文を返さない。
- `applications.health_commands`を固定ID、表示名、固定argvのローカル設定catalogとして追加した。実行ファイルは絶対pathを実行時に`resolve(strict=True)`し、NUL／空引数／長大argv／認証情報を示す文字列を設定検証で拒否する。APIはIDと表示名だけを返し、App設定はcatalog IDだけを保存するため、requestから実行path・argv・環境変数を注入できない。
- 許可コマンドHCは最大4並列、0.2〜30秒で、Web processの実command子processにせず`systemd-run --user --wait --collect`の一時serviceとして実行する。`NoNewPrivileges`、read-only system/home、PrivateTmp、CPU／memory／task／runtime上限、固定PATH、`StandardOutput/Error=null`を強制し、shellを使わない。結果は成功／終了コード／timeoutだけとし、stdout／stderrをAPI・logへ出さない。

検証: backend全432件、アプリHC／DRM集中11件、frontend TypeScript／production build成功。実systemd user transient unitで固定`/usr/bin/true`の成功を確認した。`./deck.sh`最終反映後はservice PID `328130`、active、health 200。実DBのApp 2件中running 1件でGPU／VRAM双方が取得可能だった。認証付きChromium 2件で、320／1280pxの固定catalog選択、自由argv入力なし、決定的fixtureのGPU 38%／VRAM 4.0GB表示、横overflowなしを確認した。一時user／login session／auditは清掃済み。

## Terminal V2 Phase 4c-5 物理端末証拠取得導線 準備完了（2026-07-21 11:56 JST、端末確認は継続中）

- 管理者Settingsへ追加した物理iPhone Safari／Standalone PWA専用入口から、既存Sessionへ接続せずV2 Labへ遷移できるようにした。明示的に作成したV2 Lab Sessionだけを対象とし、通常Sessionと別tabのLab SessionをV2で開かない境界を維持する。
- V2 Lab headerへ本文非収集の検証レポートを追加した。Browser／Standalone判別、secure context、layout／Visual Viewport寸法とoffset、横overflow、root containment、IME textarea数、rows／cols、replay／echo／scroll／resize／reconnectだけをその場でsnapshot化する。画面本文、入力、Clipboard、cwd、command、token、Session IDは収集・永続化せず、利用者が明示した場合だけJSONをClipboardへコピーする。
- replay未計測、echo 20 sample未満、scroll未操作を合格扱いせず、設計閾値を満たした項目だけを表示上で合格にする。E2Eへ320pxのレポート表示、合否項目、本文marker／Paste結果の非混入回帰を追加した。

検証: frontend TypeScript／production build、Playwright test discovery、diff whitespace検査に成功。実ControlDeckのChromium E2E 1件で、POST応答から直ちに所有登録した専用V2 Lab Sessionだけを使い、320pxのレポート表示、閾値判定、Terminal本文marker／Paste結果の非混入、既存の4 viewport／latency／resume／reload／session switch回帰を確認した。所有Sessionと一時user／login session／auditは清掃済みで、既存Sessionには接続・入力・終了していない。物理iPhone Safari／Standalone PWAそれぞれの操作確認とJSON証拠は未取得であり、Phase 4c-5は未完了、canary／V2既定化は未着手のまま維持する。

## Terminal V2 Phase 4c-4 自動viewport／latency回帰 完了（2026-07-21 11:36 JST）

- V2 rootへ本文・入力内容を含まないmount内telemetryを追加した。initial／resumeごとのreplay総時間、write drain、最終2 paint、履歴byte／chunk、通常入力送信から次のPTY outputまでのp95／max／sample数、local scrollのpaint時間、resize／reconnect回数、最終rows／colsだけを保持する。最大128 sampleの数値だけを有界保持し、文字、Clipboard、cwd、command、tokenは記録しない。
- 320×700、390×844、768×1024、1280×800を同じ専用Lab Sessionで再mountし、各幅でLIVE、入力echo、Visual Viewport containment、横overflow 0、単一IME textarea、最小rows／cols、mobile helper表示／desktop非表示を検証する自動回帰を追加した。390pxでは高さ430pxへ縮めるkeyboard相当のgeometry変化と復帰を行い、root／helper下端、PTY resize、textarea個数を確認する。
- 設計の44px touch targetに対して、V1／V2双方のsession selectorとmobile Paste／Enter／helper keyが32〜40pxだった不一致を修正した。全viewportで可視button／selectを実寸検査し、V1のPaste／Copy／helper focus、geometry event集約、keyboard開閉10往復も同時回帰した。

検証: Terminal backend集中29件、frontend TypeScript／production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、Playwright ChromiumのV2 Lab 1件で4 viewport、20回の1-byte echo p95 50ms未満／max 250ms未満、scroll p95／max 100ms未満、replay 4秒未満、履歴scroll→WebSocket resume、page reload、keyboard相当resize、全可視操作44px以上、横overflowなし、console／page errorなしを確認した。V1代表回帰3件も成功した。検証用Session／user／session／audit／一時credentialは清掃済み。物理iPhone Safari／PWAはChromium emulationと区別して未完了であり、証拠が得られるまでcanary／既定化へ進めない。

追補: Linux Playwright WebKitでも同じ4 viewport／latency／resume／reload／session switch回帰を実行し、作成直後のquery refetch前に旧`select` optionsが残り得るbrowser間raceを検出した。Terminal Session作成は一覧再取得完了後だけ全画面viewをmountするよう修正した。E2E cleanupは一覧や選択値からIDを推測せず、自身が送ったPOST responseのIDだけを直ちに所有集合へ登録し、その集合以外への接続・入力・削除を行わない。WebKitが未対応の`interactive-widget` viewport hintを仕様どおり無視した既知診断だけを除き、console／page errorなしを維持する。これはSafari系engineの自動証拠であり、物理iPhone／standalone PWAの代替にはしない。

## Terminal V2 Phase 4c-3 UI／操作同等化 完了（2026-07-21 11:16 JST）

- V2専用Lab SessionへV1と同じTerminal workspace契約を接続した。PC Copy、モバイルPasteタップ／上swipe Copy、Enter、Esc、Tab、Ctrl、矢印、`^C`／`^D`／`^Z`／`^L`、Clipboard paste、32KiB以上のchunk／ACK／cancel／retry進捗、本文swipe、右端history bar、Snippet／Automation panel、session switchを同じaccessible nameと操作位置で提供する。
- 通常keyとhelperは履歴表示中だけlocal xterm buffer末尾へ戻して直接送信する。Pasteは既存の有界controllerを再利用し、UTF-8 byte境界、bracketed paste、大容量送信中の再接続を扱う。Copyは選択範囲を優先し、未選択時だけ最大100,000行のlocal bufferを結合する。本文swipeと右端barはlocal bufferだけを動かし、tmuxへの二重scroll命令を送らない。
- Lab tab内の専用Session一覧と切替を維持し、通常Sessionや別tabのLab SessionをV2へ接続しないfail-closed境界は変更していない。既定は引き続きV1で、物理iPhone Safari／PWAを含む全viewport計測、canary、V2既定化、V1ロールバック確認は後続項目として残す。

検証: Terminal backend集中29件、frontend TypeScript／production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、Playwright Chromium 1件で320×700の専用Lab作成、全helper key byte列、大容量UTF-8 Pasteのbyte数／SHA-256一致、Copy、Automation、120行local history、WebSocket再接続、page reload、同一tabの2 Session切替、4秒以内のLIVE、横overflowなし、IME textarea 1個、console／page errorなしを確認した。検証用Session／user／session／audit／一時credentialは清掃済み。

## Terminal V2 Phase 4c-2 core／専用Lab Session 完了（2026-07-21 11:02 JST）

- 既定V1を変更せず、`?terminalLab=v2`でそのbrowser tabが新規作成した専用Sessionだけを`XtermViewV2`へ接続する並行Lab経路を完成させた。V2は独立WebSocket generation、initial／resume、journal sequence、reset fallback、受信順write scheduler、最大32KiBの描画slice、最終2 paint境界、単一xterm instanceを持ち、PTY output全量をReact stateへ入れない。
- V2 inputは通常keyをlocal history末尾へ戻して直接送信し、大容量Pasteは既存のchunk／ACK／cancel／retry controllerを再利用する。Visual Viewportのwidth／height／offset、composition中のresize保留、xterm local resizeとPTY resize generation、単一IME textareaを維持する。本文swipeと右端barはtmux copy-modeへ命令せず、同じlocal xterm bufferだけを操作する。
- Lab属性をfrontendの記憶だけにせず、tmuxのsession-local `@control-deck-engine=v2-lab`へ永続化した。API一覧とfallback PTYも同じengine契約を返し、V2 WebSocketはLab属性のないSessionをattach前に4403、V1 WebSocketはV2 Lab Sessionを4403で拒否する。別tabが作ったLab Sessionは通常一覧／切替候補へ出さず、同一tmux SessionへのV1／V2混在をfail-closedにした。不明engineの作成は422で拒否する。

検証: backend全425件、terminal集中23件、frontend TypeScript／production build成功。実ControlDeckのPlaywright Chromium 1件で320×700の専用Lab作成、4秒以内のLIVE、通常入力echo、WebSocket切断後の差分resume、同一local履歴、ページreload後の同じSession ID／履歴、IME textarea 1個、横overflowなし、console／page errorなしを確認した。さらに実tmux Lab Sessionへ履歴markerを出力して`control-deck-web`を再起動し、永続Lab属性、同じSession ID、capture replay内markerを確認した。検証用Session／user／session／audit／一時credentialは清掃済み。既定は引き続きV1で、V2のPaste／Copy／helper／swipe／Automation／session switch全操作同等化、全viewport計測、物理iPhone Safari／PWA、canary／既定化は後続項目として未完了のまま維持する。

## Workflow Phase 5b sample／全node docs／指定回帰flow 完了（2026-07-21 10:52 JST）

- SampleBookを14件から19件へ拡張し、`Execution Time Travel`、`Local LLM Runtime Route`、`PC State Recovery`、`AI Diagnose & Patch`、`Regression Batch`を追加した。全sampleへ目的、難易度、所要時間、capability／副作用／Secret・model・App要件、型付き入出力、sample入力、期待assert、mock、node walkthrough、failure injection、recovery／retry、install前previewを同じ契約で付与した。全件をコピー直後にpreview／publishでき、外部依存のないsampleは最終typed outputまで実行できる。
- backend node catalogを正として、全62 nodeへ目的、使う／使わない場面、全設定項目、型付き入出力、変数例、副作用、権限、Secret方針、retry／timeout／error route、代表Error、性能／cost、2 recipes、migration noteを構造化して返すようにした。SampleBookのNode Referenceはこのcanonical metadataを表示し、frontend側の説明欠落に依存しない。
- SampleBook UIへ動的category、install前preview、型／入力例／期待結果、failure／recoveryを追加した。320pxは一覧と詳細を切り替えるbottom-sheet相当、PCは一覧と詳細の2 paneを維持し、既存のコピー→Editor導線へ接続した。

検証: backend全423件、SampleBook／node docs集中16件、frontend TypeScript／production build成功。全19 sampleの構造・安全preview・公開、全62 nodeの15 sectionと2 recipes、Regression Batchの5件→3 batch、Time Travelの同一入力によるv1 historical／v2 current差、意図的timeoutの決定的診断→operation patch選択適用→成功、永続state進捗を確認した。実serviceを`./deck.sh`で再起動してactive／health 200を確認し、Playwright Chromium 1件で320pxのinstall前preview→コピー→公開→実行、1280pxの全node doc section、両幅の横overflowなし、console／page errorなしを確認した。さらに実行を`WAITING`にしたまま`control-deck-web`を実再起動し、同じexecution IDが`SUCCEEDED`／progress 1へ復帰した。実Ollamaのloaded `qwen3.6-27b-q5_k_m:latest`を`ai.route`が検出・選択し、動的endpoint／modelで回答まで成功した。検証用Workflow／execution／state／user／session／audit／一時credentialは清掃済み。

## Workflow Phase 5 AI diagnose／patch／runtime route／Project Intelligence 完了（2026-07-21 10:32 JST）

- Editorへ`Project Intelligence`を追加した。保存済み定義、直近20 execution、node別実行数／失敗率／平均時間、実行順、side effect、semantic issue、不明なLLM設定、test case、接続済みApp Studio Projectを統合し、稼働中provider／modelとGPU／空きVRAMも同じ読み取り専用reportへ示す。センサーやruntime取得失敗は空候補／N/Aへ縮退し、Editor全体を停止しない。
- 失敗実行のdiagnose APIは、Workflow definitionをSecret-safe snapshotへ変換し、選択実行から失敗node、typed error、redacted runtimeだけを最小payloadとしてLLMへ渡す。応答は原因、確信度、最大3案のversion 1 operation patchに限定し、各案を構造／semantic／Secret境界で再検証してから差分を表示する。model停止、不正JSON、無効operation、案なしではtimeout／retryを扱う決定的診断へfallbackする。ローカルruntimeの単一案平坦応答と`update_node.changes.config`はcanonical `set_config`へ限定正規化し、その後に同じ厳格validatorを通す。
- patchはnode ID基準の`set_config`／`update_node`／node・edge追加削除だけを許可し、最大100操作／256KiB、literal Secret拒否、trigger削除拒否、構造検証、semantic error停止、RFC 6902形式exportを実装した。previewは保存せずbefore／after quality・件数・警告を返し、利用者が選択した案だけをoptimistic timestamp付きで適用する。適用前version snapshotとchecksum／件数だけの監査を残し、値やpromptは監査へ保存しない。trigger schemaから決定的な`AI baseline` test caseも重複なしで生成する。
- `ai.route` nodeを追加した。最大20候補または検出済みprovider／modelをavailability、selected runtime、loaded状態、context、空きVRAM、priorityとBalanced／Availability／Loaded／Context／VRAM戦略で評価し、選択した`base_url`／`model`／理由／候補評価／runtime snapshotを型付きで返す。条件を満たさない場合はtyped `AI_RUNTIME_UNAVAILABLE`、不正設定は非retry errorとする。`llm.chat`のendpoint／modelをtemplate対応に変更し、route出力をそのまま後続生成へ接続できる。
- EditorのPC command surfaceとmobile More menuから2 step以内でProject Intelligenceを開ける。品質／issue、baseline test、AIへの再検討指示、検出endpoint／model、決定的診断、案ごとのquality・警告・操作JSON、選択適用を1つのPC side surface／mobile bottom sheetへまとめた。AI案の自動適用は行わず、既存のCanvas手動編集、Undo／Redo、autosave、競合再読込も維持した。

検証: backend全418件、Intelligence／runtime route／patch／Secret安全性集中20件、frontend TypeScript／production build成功。operation atomicity、literal Secret拒否、optimistic conflict、version snapshot、監査payload非保存、baseline test重複防止、VRAM N/A、route→LLM endpoint／model template接続を確認した。実serviceを再起動してactive／health 200を確認し、Playwright Chromium 1件で320pxの実timeout失敗→ローカル診断→操作差分→選択適用→baseline test、1280pxの`ai.route`設定とlive endpoint候補を確認した。両幅で横overflow、console／page errorなし。実runtime routeはOllamaのloaded modelを1候補から選択し、context 262,144、VRAM N/Aでも停止しないことを確認した。実Ollama `qwen3.6-27b-q5_k_m:latest`へtemperature 0.1のstructured診断を行い、12.31秒、AI source、valid 1案、fallbackなしを確認した。検証用Workflow／execution／test case／version／user／session／audit／一時credentialは清掃済み。次はsample／全node詳細docs／指定E2E flow。

## Workflow Phase 3 batch／rate limit／circuit breaker 完了（2026-07-21 10:00 JST）

- `data.batch`を追加し、最大10,000件のJSON arrayを順序を保った1〜1,000件単位のbatch配列へ決定的に分割するようにした。項目数、batch数、設定件数を型付きで返し、`batches`を`flow.map`へ渡すことで一定件数単位のsubflow処理を構成できる。
- `control.rate_limit`を追加した。同じWorkflow・scopeの複数executionが固定時間窓の使用数をDB-backed stateで共有し、service再起動後も上限を維持する。最大実行数1〜10,000、時間窓0.1秒〜24時間、最大待機0〜3,600秒を検証し、上限時は空きまで非同期waitするか、typed `RATE_LIMITED`で即時error routeへ送る。取得結果は使用数、残数、reset日時、待機秒数を返す。
- `control.circuit_breaker`を追加した。同じWorkflow・scopeで`check`／`record_success`／`record_failure`／`status`／`reset`を共有し、連続失敗でCLOSEDからOPENへ遮断、回復時刻後はCAS leaseに勝った1 executionだけをHALF_OPEN probeとして許可する。probe成功でCLOSEDへ戻り、失敗で再OPENする。OPEN前から遅れて到着した成功は回路を閉じない。Editorはcheckの許可／遮断handle、状態・連続失敗・再試行日時を表示する。
- レートと回路の内部状態は既存の型付き`WorkflowStateEntry`を専用namespaceで再利用し、version CASによりSQLite同時実行時の更新欠落を防いだ。scopeは英字始まり最大64文字、Secret値の使用を拒否し、監査にはpayloadを含めずscope、操作、状態、execution／node IDだけを記録する。Workflow削除時は既存state清掃経路で同時に削除される。

検証: backend全413件、resilience／metadata／semantic validation集中19件、frontend TypeScript／production build成功。8 workerで16同時rate取得を競合させ上限4件だけが成功し、12同時HALF_OPEN checkでprobeが1件だけ許可されること、時間窓更新、OPEN遮断、probe成功／失敗、遅延成功無視、Secret非永続化、Workflow削除清掃、実executionの許可／遮断routeを確認した。実serviceを`./deck.sh`で再起動してactive／health 200を確認し、Playwright Chromium 1件で1280px Editorのbatch・rate・circuit設定と許可／遮断handle、320px公開Runnerの5項目→3 batch、連続実行の実待機、CLOSED許可、失敗記録後OPEN遮断、2秒後HALF_OPEN probe→成功復帰、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／execution／state／user／session／一時credentialは清掃済み。次はAI diagnose／patch／runtime route／Project Intelligence。

## Workflow Phase 3 version-pinned Subflow Map 完了（2026-07-21 09:41 JST）

- `flow.map`を追加し、JSON arrayの各項目を明示的に公開された同一サブWorkflowへ型付きで渡すようにした。開始時に最新published version IDとdefinition snapshotを1回だけ固定し、途中で公開版が変わっても全項目は同じversionを使う。子triggerへ`item`／`index`／`total`をネイティブ型で渡し、message／追加JSON入力も項目ごとにtemplate展開できる。結果は完了順に依存せず入力順で、子の型付きoutputs／error、execution ID、成功・失敗数、対象Workflow／versionを返す。
- itemは最大100件、並列数は1〜5、各子timeoutは10〜3,600秒、親nodeは最大2時間へ制限した。失敗方針は、実行中batchを完了して親をtyped `SUBFLOW_MAP_FAILED`で止める`stop`と、失敗を結果へ収集して残項目を続ける`collect`を明示選択する。親cancelは待機中の子taskへ伝播する。既存3段depth上限にWorkflow lineageを追加し、`flow.call`／`control.try`／`flow.map`の直接・間接cycleを子execution作成前に`SUBFLOW_CYCLE`で拒否する。
- Secretは実値のまま子入力へ渡せる一方、親Map結果のitem／子output／親子contextでは伏字化し、`workflow.subflow_map`監査にはpayloadを含めず件数、並列数、失敗方針、固定version、子execution IDだけを記録する。Editorへ公開Workflow、items、並列数、失敗方針、message、追加入力、timeoutと全出力を段階表示し、object／array code fieldを整形・型保持して編集できる。公開RunnerとEditor実行入力へ型付き`json_array`を追加した。Map内の動的待機を可能にするため、`util.wait`の秒数もtemplate展開とtyped errorへ統一した。

検証: backend全408件、Subflow Map／既存loop集中16件、Runner contract集中7件、frontend TypeScript／production build成功。実backend integrationで並列上限、入力順、stop時の後続未起動、collect成功2／失敗1、同一version固定、cycleのDB検索前拒否、Secretの親子context／監査非露出を確認した。実Control DeckのPlaywright Chromium 1件で1280px Editorの全Map設定と横overflowなし、320px公開Runnerの`json_array`から完了時間の異なる3子を並列実行し、入力順0／1／2、成功3、異なる子execution ID、同一公開version、型付き結果、横overflowなし、console／page errorなしを確認した。serviceは`./deck.sh`で反映後active、health 200。検証用Workflow／execution／user／session／一時credential／test artifactは清掃済み。次はbatch／rate limit／circuit breaker。

## Workflow Phase 3 durable business event 完了（2026-07-21 09:24 JST）

- `event.emit`から同名のWorkflow custom event triggerへ配送するDB-backed outboxを追加した。発行時にevent本体とsubscriber別deliveryを先行保存し、最新published definitionかつenabledの受信Workflowだけを起動する。event envelopeはevent ID／名前／payload／発行元Workflow・execution・node／時刻を持ち、受信実行を`event:workflow`として履歴から判別できる。Editorはevent種別に応じてalert filterとcustom event名を段階表示し、発行nodeのobject payloadを整形・型保持して編集できる。
- payloadはJSON object・64KiB以内、event名は英字始まり最大128文字、subscriberは100件、連鎖は8 hop、outboxは10,000件へ制限した。lineage内Workflowを除外して直接・間接cycleを止める。Secretをpayloadへ含めても永続化前に伏字化し、発行／配送監査へ本文を含めない。配送例外は型名だけを保存し、例外本文に含まれ得る秘密値をDBへ残さない。
- 未完了deliveryはservice内loopが最大3回まで再送し、`DELIVERING`のまま停止した状態も同じevent IDで回収する。成功、部分失敗、失敗をsubscriber集計し、event ID／状態／購読・配送・失敗数／受信execution ID／失敗Workflow IDをnode出力へ返す。完了eventは7日後にdeliveryとともに清掃し、Workflow削除時も外部キー順に清掃する。Alembic `c2f8a6d53b91`にoutbox／delivery table、一意event ID、一意subscriber delivery、外部キーと検索indexを追加した。

検証: backend全405件、business event／migration集中10件、frontend TypeScript／production build成功。実ブラウザのPlaywright Chromium 1件で1280px Editorの受信種別段階表示／発行payload、320px公開Runnerから動的nested payloadを発行し、別の公開・有効化済みWorkflowが受信して型付き結果を返すところまで確認した。両幅で横overflow、console／page errorなし。実DBへ`DELIVERING`・attempt 1の配送を残してserviceを停止し、`./deck.sh`再起動直後にattempt 2で`DISPATCHED`、受信実行`event:workflow`、結果`recovered=after-restart`を確認した。Secret本文の非永続化、3回上限、保持清掃、migration upgrade／downgradeも自動試験済み。検証用Workflow／execution／event／delivery／user／sessionは清掃済み。次はsubworkflow map。

## App Studio Workflow契約ベース自動アプリ化 完了（2026-07-21 09:07 JST）

- WorkflowからProjectを作る2経路を、空の`pages: []`ではなく動作可能な推奨Application Specの自動生成へ変更した。trigger入力とoutput契約を同じJSON Schemaから取得し、型に応じたtext／number／boolean／select／JSON入力、同期Workflow endpoint、API-key認証、結果renderer、Home navigation、responsive Stack／Cardを初期適用する。生成runtimeが未対応のWorkflow固有schema拡張は安全に除外し、Editorの提案と生成ASP.NETフォームが同じrequest／response schemaを正とする。
- `xAppAdvisor`へ提案根拠となる入出力・control型・ready状態を保持し、App Studioは「自動構成済み」、入力／出力一覧、契約フォームをCanvas上で表示する。主要導線を「生成・動作確認へ」とし、ユーザーがゼロからPageを設計せず、そのままSource Preview／Buildへ進めるようにした。
- 気に入らない場合は「AIに再検討」からWorkflow契約と実行endpointを明示したpromptでSimple／Balanced／Denseの3案を生成し、既存のvisual diff／静的検証／選択適用を経由する。初期指示をWorkflow動作維持の再検討へ設定した。自由codeや自動適用は行わず、invalid案はApply不可のまま示す。Canvas Inspector、component追加、Undo／Redo、単一Saveによる任意修正も維持した。
- Workflowからの作成画面を「ほぼ自動でアプリを作成」として整理し、Draft／公開版のどちらも作成直後に動作可能なGUIへ進む。公開版bindingとimmutable version IDの既存境界、手動で与えたApplication Spec、WorkflowなしProjectの空Canvas互換は維持した。

検証: backend全401件、Application Builder集中33件、frontend TypeScript／production build成功。実serviceを再起動しAlembic `c2f8a6d53b91` headとservice activeを確認した。実Control DeckのPlaywright Chromiumで320pxの自動構成、text／number／boolean入力、出力、AI再検討の初期指示、mobile Inspectorによる手動修正導線、1280px再読込、生成workspace遷移、両幅の横overflowなし、console／page errorなしを確認した。実Ollama `qwen3.6-27b-q5_k_m:latest`へWorkflow契約付き再検討を依頼し、Simple／Balanced／Dense 3案（Patch 2／3／3件、静的検証valid 2案／Apply停止1案）をschemaどおり取得した。公式.NET SDK 8.0.423を一時配置し、自動Specの15-file ASP.NET sourceをwarning-as-errorでbuild（warning／error 0）、generated self-test成功、実Kestrelでhealth 200、未認証401、認証API 200を確認した。さらに実ブラウザで320pxからMessage／Count／Enabledを送信し`answer=Automatic app works`を結果regionへ表示、320／1280pxのSafe Areaと横overflowなし、console／page errorなしを確認した。一時SDK／生成物／server／テストuser／session／Workflow／Projectは削除またはTrashへ移動した。

## Workflow Phase 3 typed/versioned durable state 完了（2026-07-21 08:24 JST）

- `data.state`をWorkflow単位の期限なしDB-backed stateとして追加した。get／set／delete／incrementを提供し、同じnamespace／keyでもWorkflow間を分離する。初回setでstring／number／integer／boolean／object／arrayの型を固定し、型変更はdelete後だけ許可する。実行内`var.set`、browser reloadで消えるApplication Builder client state、TTL付きcacheとは別のserver state境界とした。
- 各stateは1から始まる単調versionを持つ。expected version 0はcreate-only、1以上は一致時だけset／increment／deleteするcompare-and-setとして扱い、不一致は`STATE_VERSION_CONFLICT`へ明示する。expected version未指定でもDB更新をversion条件付きにし、内部競合だけを上限付きretryする。integer／numberのincrementはread-modify-writeを同じCASで保護する。
- namespaceは英字始まりの最大64文字、keyは最大128文字、valueは256KiB、1 Workflow 10,000 keyへ制限した。valueは永続化前にSecretをredactし、Secret値をkeyへ含める操作は拒否する。set／increment／delete監査にはpayloadを含めずnamespace、key、entry ID、version、type、execution／node IDだけを記録する。Workflow削除時はstate entryをexecutionより先に清掃する。
- Editorへ操作、namespace、key、型、JSON value、increment delta、expected versionを設定する型付きnodeと詳細helpを追加した。Alembic revision `b7e1d94c2f60`でstate table、Workflow・execution外部キー、一意keyと検索indexを追加した。

検証: backend全400件、state／migration／dry-run集中13件、frontend TypeScript／production build成功。型固定、create-only、stale version拒否、delete後の型変更、Secret安全性、256KiB境界、8 worker・32同時incrementの欠落なし（value 32／version 33）、同一versionへの8同時CASが成功1／競合7となることを確認した。実DBはupgrade前backupとmanifestを0600で保存しSHA-256一致、revision headを確認した。実serviceへinteger stateをset後に`control-deck-web`を再起動し、expected version付きincrementで40→42／version 1→2を確認した。Playwright Chromium 1件で320px公開Runnerのset／increment、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／user／session／audit／state entry／一時credentialは清掃済み。次はevent。

## Workflow Phase 3 durable TTL cache 完了（2026-07-21 08:14 JST）

- `data.cache`をWorkflow単位のDB-backed TTL cacheとして追加した。set／get／delete／sizeを提供し、同じnamespace／keyでもWorkflow間を分離する。TTLは必須の1秒〜30日（既定1時間）とし、期限切れをget／sizeで返さずlazyに物理削除することで、期限なしの次項目`state`と責務を分けた。
- namespaceは英字始まりの最大64文字、keyは最大128文字、valueは256KiB、1 Workflow 10,000 keyへ制限した。valueは永続化前にSecretをredactし、Secret値をkeyへ含める操作は拒否する。set／delete監査にはpayloadを含めずnamespace、key、entry ID、execution／node ID、件数だけを記録する。Workflow削除時はcache entryをexecutionより先に清掃する。
- Editorへ操作、namespace、key、JSON value、TTLを設定する型付きnodeと詳細helpを追加した。Alembic revision `a4d9c73b1e52`でcache table、Workflow・execution外部キー、一意keyと期限・検索indexを追加した。

検証: backend全397件、cache／migration／dry-run集中13件、frontend TypeScript／production build成功。TTL失効、上書き、Workflow分離、削除、Secret安全性、256KiB／30日境界、8 worker・32同時setの単一key収束を確認した。実DBはupgrade前backupとmanifestを0600で保存しSHA-256一致、revision headを確認した。実serviceへset後に`control-deck-web`を再起動し、同じ値をgetできることを確認した。Playwright Chromium 1件で320px公開Runnerのset／get、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflow／user／session／audit／cache entry／一時credentialは清掃済み。次はstate／event。

## Workflow Phase 3 durable queue 完了（2026-07-21 08:04 JST）

- `data.queue`をWorkflow単位のDB-backed durable FIFOとして追加した。enqueue／dequeue／peek／sizeを提供し、同名queueでもWorkflow間を分離する。名前は英字始まりの最大64文字、itemは最大256KiB、1 queue 10,000件へ制限した。payloadは永続化前にSecretをredactし、enqueue／dequeueの監査にはpayloadを含めずitem ID等のmetadataだけを記録する。
- dequeueはSQLiteの単一`DELETE ... RETURNING`で先頭itemの選択と削除をatomicにし、lock／採番競合だけを上限付きでretryする。8 workerから32件を同時取得して欠落・重複がないことを確認した。Workflow削除時はqueue itemをexecutionより先に清掃する。
- Editorへ操作、queue名、enqueue値を設定する型付きnodeを追加し、公開Runnerと同じ実行contractへ接続した。Alembic revision `f310a4c29d7b`でqueue table、Workflow・execution外部キー、FIFO順序と容量検査用indexを追加した。

検証: backend全394件、queue／migration／dry-run集中12件、frontend TypeScript／production build成功。実DBはupgrade前backupとmanifestを0600で保存し、SHA-256一致、revision headを確認した。実serviceへenqueue後に`control-deck-web`を再起動し、同じ値をdequeueできることを確認した。Playwright Chromium 1件で320px公開Runnerのenqueue／dequeue、1280px Editor設定、再度320px Editor、両幅の横overflowなし、console／page errorなしを確認した。検証用Workflowは削除済みで、検証user／session／audit／一時credentialも清掃した。次はcache／state／event。

## Workflow Phase 3 durable human.form 完了（2026-07-20 23:05 JST）

- `human.form`を既存`WorkflowPause` checkpoint基盤へ追加した。承認と区別する`form`種別で入力待ちをDBへ保存し、ControlDeck再起動・in-memory task消失後も同じexecution IDから継続する。送信は通常経路、キャンセルはerror、期限切れはtimeoutへ進み、待機中の実行cancel、担当ユーザー限定、human interaction別の監査actionへ対応した。
- Editorは既存の型付き入力定義を再利用し、最大20項目のtext／paragraph／number／boolean／select／multi-select／date／datetime／JSON／key-value、必須、説明、初期値、最大長を設定できる。重複・不正key、未対応型、選択肢不足は公開前にblockingとする。Secret入力とfile uploadは永続フォームへ保存せず明示的に未対応とした。
- 実行時は入力定義をJSON Schemaへ変換し、Editor Debuggerと公開Runnerで同じresponsive formを描画する。必須値が揃うまで送信を無効化し、serverでも型・enum・追加field・64KiB上限を再検証する。入力は`{{form.response.field}}`から型付きで参照でき、既存`human.approval`のAPI response形と画面文言は後方互換を維持した。

検証: backend全392件、human pause／flow集中9件、frontend TypeScript／production build成功。submit、未完成入力のcancel、期限切れのerror／timeout経路を回帰した。実serviceでフォーム待機中に`control-deck-web`を再起動し、同一executionのpending form復元→schema検証済み送信→Return完了を確認した。Playwright Chromium 2件で既存承認回帰と、320px公開Runnerの必須／text／select／boolean送信、1280px結果表示、Editorのフォーム設定、両幅の横overflowなしを確認した。検証用Workflow／user／login session／audit／一時credentialは削除済み。

## Terminal mobile復元／入力／keyboard回帰 修正（2026-07-20 22:26 JST）

- 通信に問題がない状態でも、通常の1文字入力とBackspaceのたびに`scrollToBottom()`を実行して100,000行対応scrollbackを再描画していた。入力hot pathから除外し、実際に履歴中の場合だけ一度末尾へ戻すようにした。320pxで文字echo／削除を各5回実測し、10回すべて約13〜18msで反映した。
- tmuxの100,000行履歴は削除せず、Web初期復元だけを最新10,000行かつ512KiBの小さい方へ限定した。従来の最大4MiB一括転送・parser解析によるmobile main thread占有を回避し、実serviceの3,000行履歴再接続は412msでLIVEに戻った。
- software keyboardでVisual Viewportが縮小した際、backend resize ACK後までxtermの行数更新を待っていたため、待機中の入力行がkeyboard背面にclipされていた。先行outputとの順序をwrite queueで保ったxtermを先に可視領域へ合わせ、PTY側ACKまではinput FIFOだけを保持するようにした。表示位置と端末サイズの両方を崩さず、キー入力を不要に待たせない。

検証: Terminal backend 21件、backend全390件、frontend TypeScript／production build成功。実serviceのChromium 320pxで履歴再接続412ms、文字／Backspace応答13〜18ms、keyboard相当の430px高さでroot／helper／input行が可視範囲内、本文swipe、右端bar、IME composition、10回開閉、再接続／reload、100／300KB Paste、session切替、1280px Copy／remountを確認。Terminal E2Eは24成功、任意の10分soak等2件skip。検証用user／login session／audit／Terminalは清掃し、既存`490b7fd0`／`502a6ad9`とFrameDeck PID 183796を維持した。

## Workflow Phase 3 durable Delay／Try境界／System Trigger 完了（2026-07-20 21:50 JST）

- `control.delay`を承認と同じDB checkpoint基盤へ実装した。0.1秒〜7日を指定でき、実行を`WAITING`として永続化し、ControlDeck再起動・task消失後も同じexecution IDから期限到来時に再開する。待機中cancelに対応し、完了済み上流nodeを再実行しない。pause recoveryはexecutionごとの最新pauseだけを消費するため、Delay後に承認が続く場合も古いDelayを再消費しない。
- `control.try`は公開済みサブフローを実行する明示的な失敗境界として追加した。子実行の成功は`success`、typed error／失敗は`error`へ分岐し、execution ID、status、outputs、Error Contextを返す。擬似的な同時finallyは設けず、共通後処理は両branchを`control.merge`へ接続する順序が明確な構成とした。
- 既存`trigger`へ`system` modeを追加し、GPU／VRAM／diskのアラート発火、管理アプリのsystemd状態変化、llama-server状態変化、許可root内のfile作成・変更・削除を実イベントとして接続した。初回観測では発火せず変化時だけ起動し、file pathは`Path.resolve()`相当＋allowed root／deny root検証を通す。イベントpayloadは32項目・各2,000文字へ制限し、token／secret等のキーを除外する。
- schedule／Webhook／alert／systemの自動起動対象判定を編集中draftから最新published definitionへ統一した。Webhook tokenは公開履歴へ平文保存せずSHA-256だけを保持・照合する。EditorにはSystem監視対象、file path、Delay、Tryの設定とsuccess／error handleを追加し、全selectへID／accessible nameを付与した。「ディスク監視イベントを記録」sampleも追加した。

検証: backend全387件、Phase 3集中42件、frontend TypeScript／production build成功。実serviceのChromium 320pxでfile変更 → `system:file` → durable Delay → published Try subflow → success Returnを完走し、System／Delay／Try inspector、両branch、横overflowなし、console／page errorなしを確認した。App長時間稼働表示と推奨設定UIも同時回帰成功。検証用workflow／file／user／session／auditは清掃し、既存Terminal session 2件とFrameDeck PID 183796は維持した。

## Terminal入力遅延／App稼働時間フレーム／Workflow明示フロー制御（2026-07-20 21:17 JST）

- iPhone接続先への実測はRTT通常5〜12ms、packet loss 0%で、文字入力とBackspace遅延の主因が回線ではないことを確認した。software keyboard表示に伴うPTY resize中は入力順序保護barrierがSIGWINCH後の再描画を最大125ms待っていたため、resize ACKをxtermへ受信順にcommitした直後にFIFO入力を解放するよう変更した。ACK前は引き続き入力を保持し、local cell size確定前のechoは許可しない。
- Apps cardの稼働時間／開始日時からborder・背景の独立frameを外し、44px操作buttonと同じ行高を持つ等幅数字の補助情報へ変更した。Web／停止等の操作とは色と形で競合せず、長時間稼働でもcard高と左右位置を維持する。
- Workflow Phase 3へ終端専用`flow.return`、非retryのtyped `flow.error`、副作用なし`flow.note`、決定的`test.assert`を追加した。error code／retryable／有限detailsを標準Error Contextとnode runへ保存し、error branchから`{{node.error.code}}`等で参照できる。Returnの後続edgeはengine／semantic checkで拒否し、Editorでもsource handleを表示しない。外部サービス不要の「入力ガードと明示 Return」sampleを追加した。

検証: backend全384件、flow／terminal集中43件、frontend TypeScript／production build成功。実serviceへ反映し、Chromium 320pxでresize ACK→local commit直後のFIFO入力解放と、Dark／OLEDのApp cardを320／1280pxで確認した。既存Terminal session 2件とFrameDeck unitは維持。一時E2E user／login session／audit、E2E所有Terminalは限定清掃した。

## Terminal復元／mobile keyboard／scroll性能回帰 修正（2026-07-20 21:03 JST）

- tmux本体の100,000行履歴は維持しつつ、Web接続時にxtermへ一括復元するsnapshotを最新4MiBへ限定した。古い履歴をterminal sessionから削除せず、モバイルbrowserのparserを最大16MiBで長時間占有していた経路だけを除いた。
- Visual Viewportの連続resizeでgeometry timerを毎回後ろ倒しにする処理を、先頭eventから有界時間で処理するleading-edge coalescingへ変更した。外枠はIME変換中でも可視viewportのheight／offsetへ即時追従し、xterm cell／PTY resizeは変換終了まで固定するため、入力行と補助barがsoftware keyboardの背面へ残らない。
- 本文swipe時に毎frame実行していた履歴barの同期rect計測を廃止し、ResizeObserverで寸法をcache、onScroll表示更新をanimation frame単位へ集約した。本文gestureはlocal xterm scrollのまま1.35倍の軽い加速を加え、右端bar、入力focus、tmuxへの履歴移動をhot pathへ混ぜていない。

検証: Terminal backend 21件、backend全381件、frontend TypeScript／production build成功。実serviceのChromiumで320pxの初期描画、本文swipe 100ms未満、3000行再接続4秒未満、縮小viewport内の入力行、IME中のcell固定、keyboard相当の開閉10回で再接続／履歴再送なし、大容量Paste、session切替、1280px Copy/remountを確認した。Terminal E2Eは22成功＋修正後focused 1成功、service reloadと10分soakの2件は安全条件によりskip。既存`490b7fd0`／`502a6ad9`とFrameDeck PID 183796・起動時刻は前後で不変。

## Workflow durable pause／Artifact offload 完了（2026-07-20 21:03 JST）

- `WorkflowPause`をDB-backed checkpointとして実装し、承認文、担当者、JSON Schema入力、期限、応答、状態を永続化した。tokenは平文を保存せず生成直後にSHA-256化する。WAITING executionはservice再起動後も同じexecution IDで承認／却下／期限切れ／cancelでき、完了済み上流nodeを副作用込みで再実行しない。
- Editor／公開Runnerへ同じschema-driven承認フォームを追加し、string／number／integer／boolean／enum、required、64KiB応答上限、server側JSON Schema検証を共通化した。pause／resume eventをdurable sequenceへ記録する。
- `WorkflowArtifact`を追加し、redact後の即値が256KiBを超える場合はapplication-owned 0700 rootへ0600・atomic・no-followでJSON保存し、DB／execution contextにはfilename、MIME、size、SHA-256とIDだけを残す。1値32MiB／1node 20件を上限とし、containment、symlink、size、checksumをdownload前に再検証する。認可付き一覧／download、no-store／nosniff、監査、workflow削除時の実file清掃、UI download導線を実装した。
- Alembic revision `e2689f3f0c28`でpause／artifact tableを追加した。versioned DBのupgrade前にも検証付きSQLite backupを必須化し、実DBはbackup checksum一致後にrevisionを適用した。

検証: durable pauseの実service再起動越し承認、同一execution完了、上流trigger 1回を確認。pause／artifact／event集中7件、migration 4件、backend全381件、frontend build、320px公開Runner承認E2E成功。実DBは`e2689f3f0c28`、一時workflow／artifact／userは清掃済み。

## App長時間稼働表示／Dark・OLED視認性、主要UI整理（2026-07-20 20:19 JST）

- Apps cardを名称headerと、状態・稼働情報＋操作のcompactな2段へ再構成した。`実行中`等の状態badgeは折返し禁止とし、長時間稼働は`稼働 114日 7時間`、起動時刻は現在年を省略した`開始 3/28 10:23`として別々に読める。CPU／RAMはPCだけの補助値、モバイル操作はaccessibleなicon中心にし、44px hit areaを保ちながら長時間稼働cardを150px以下に収めた。
- Dark／OLEDではcard、稼働情報面、名称、主要値、補助値、操作buttonの背景色と文字色を明示し、黒背景で文字色が暗く継承される経路をなくした。FrameDeckの実process／systemd unitへ操作せず、API応答だけを114日稼働相当に置換するE2Eで再現した。
- Terminal一覧は3点menuを廃止して`🔧` Automationと`🗑️` Deleteを44pxの直接操作にした。workload／Web接続状態と、最終活動／作成日時・接続操作を別段に分け、複数badge時にも日付とbuttonが重ならない。Workflow nodeの推奨設定面は白へ寄らないsolid accent面＋白文字＋白buttonに変更し、Terminal Snippet editorもlight／dark双方で背景、border、本文、focusを明示した。

検証: frontend TypeScript／production build成功。Playwright ChromiumでFrameDeck相当の長時間稼働cardをDark＋OLEDの1280／320pxで確認し、状態1行、稼働面と操作段の非重複、横overflowなし、非黒文字色を検証した。Terminal／Snippet／Workflow視認性E2E 3件も320／PCで成功。検証用user／login session／auditは削除し、実FrameDeckと既存Terminal session 2件は変更していない。

## Alembic schema migration／既存SQLite保全 完了（2026-07-20 20:08 JST）

- Alembicを導入し、現行34 tableを明示するbaseline revision `dd6115224a90`を作成した。起動時は排他lock下でrevisionを検証・upgradeし、既存unversioned DBはSQLite backup APIでsnapshotを取得してからbaselineをadoptする。管理CLIは稼働中serviceと並行してschemaを変更せず、起動lifespanだけをmigration境界とする。
- backup前に空き容量を検査し、backup DBとmanifestを0600、directoryを0700で保存する。source／backup双方の`integrity_check`、`foreign_key_check`、全table件数、SHA-256を照合する。head revisionでもmodel table／columnとDB readを検証し、schema driftを`create_all`で隠さず起動失敗にする。旧light migrationは移行期間の互換no-opとして残した。
- 実DBをrevision管理へ移行し、`control-deck-pre-alembic-20260720T105852Z.db`とchecksum manifestを検証した。2回目以降の起動でbackupは増えずrevision一致を確認した。Alembicのlogging設定がapplication loggerを抑制しないよう統合した。

検証: backend全375件、migration subprocess 3件、migration＋auth 11件、`alembic check`成功。実serviceの再起動、health 200、revision／35 table、backup checksum／integrity／row count、再起動冪等性を確認した。

## Workflow Execution durable event／SSE 完了（2026-07-20 19:49 JST）

- `WorkflowExecutionEvent`と実行ごとの`last_event_sequence`を追加し、開始、状態遷移、node開始／終了、実行終了をDB commit後の単調sequenceとして保存する。payloadは再帰redact後64KiB、1実行2,000件、7日保持に制限し、並列DAGのsequence採番を直列化した。観測系の保存失敗でworkflow本体は停止しない。
- 認証・`workflows.edit`権限付きの`GET /workflow-executions/{id}/events?after_sequence=N`とSSE `.../stream`を追加した。`Last-Event-ID`／query cursorから最大200件ずつ欠落分を再送し、保持範囲外は`stream.reset`、15秒heartbeat、終了時は`stream.closed`を返す。同一Origin、no-cache、proxy buffering無効を強制した。
- Execution Debuggerは実行中の1.2秒ポーリングを廃止し、EventSource到着時だけlive detailと履歴cacheをまとめて更新する。切断中だけ3秒fallback、外部scheduleの新規実行発見は15秒、接続状態は小さなLive／Connecting indicatorで示し、通常操作を増やしていない。
- workflow削除時はeventをexecutionより先に削除するFK順序へ更新した。既存SQLiteは移行時の互換処理でsequence列を補い、新規event tableを作成した。schema管理は上記Alembic baselineへ移行済み。

検証: backend全372件、event focused 2件、frontend TypeScript／production build成功。実ControlDeck serviceを再起動しhealth正常、Playwright Chromiumで認証付きSSE接続、実workflow完了追従、320／1280px横overflowなし、console／page errorなしを確認した。再試験後journalにevent／workflow errorなし。検証用workflow／execution／event／user／sessionは削除済み。

## Application Builder Phase B3 Isolated Build／Self-test 完了（2026-07-20 19:31 JST）

- 保存済みSpecとWorkflowからserver側で再生成した決定的Source ZIPだけを入力に、C# Console／ASP.NET Coreを一時systemd user unitでoffline restore、warning-as-error build、生成self-testするdurable Build Jobを実装した。SDK allowlist、venv launcher、64MiB ZIP／500 file／128MiB展開、regular relative path、重複・暗号化・symlink・escape拒否を強制する。
- unitはbuild ID固有名とapplication-owned 0700 rootを持ち、read-only system／home、root限定write、NoNewPrivileges、IPv4／IPv6 network fail-closed、2GiB memory、128 tasks、CPU 200%、60〜3,600秒timeoutを固定argvで適用する。SDK子processの環境は専用HOME／TMP／NuGet、固定PATH／localeだけにし、Control Deck config／Secret／PYTHONPATHを継承しない。
- DBとatomic state fileでphase、exit、resultを追跡し、同時2件／Projectごと1件、実systemctl cancel、service中断検出、1MiB redacted journal、source／binary artifact、checksum、認証download、active削除拒否、監査を実装した。unit名と期待rootはID取得直後の最初のcommitへ含め、root作成前に停止した失敗記録も他unitへ触れず削除できる。
- App StudioのExport workspaceへ`Build & test`を主操作として置き、SDK／systemd／network／並列数、phase progress、Cancel、artifact、3点menu内log／Deleteを段階表示する。削除だけ確認し、320pxでは縦積み、PCでは既存workspace内cardに収めた。Linux／Windows向け生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。

検証: backend全370件、Application Builder focused 41件、frontend TypeScript／production build成功。実Control Deck serviceのPlaywright Chromiumで320pxからConsole buildを開始し、network probeを通過したoffline restore／build／self-test、phase、source／binary、checksum付きdownload、log、Deleteを確認した。2件目を実行中にCancelしてsystemctl stop／Cancelledを確認し、ASP.NET Coreも同じ隔離unitでbuild／self-test／binaryまで完了した。1280px横overflow、console／page errorなし。journalでvenv worker、self-test pass、resource使用量、cancel stopを確認した。検証用build root／unit／Project／Workflow／user／login session／auditを削除した。

## Terminal presentation boundary／Snippet Automation 完了（2026-07-20 19:18 JST）

- 18:20 checkpointの未解消項目は解消した。frontendは履歴復元後にxtermの端末自動応答だけをoverlay下で送信し、backendの`presentation_input_start`／`presentation_sync`が観測したPTY output sequenceをxtermが描画し終えるまでLIVE表示へ移らない。固定delayは使わず、初回接続と3,000行履歴の再接続で未完成frameを公開しない。補助key／Paste／本文swipe／右端scrollbarの回帰も維持した。実sessionが存在する環境でのservice reload E2Eは安全guardにより実行せず、通常の`./deck.sh`再起動前後で既存2 sessionが不変であることを確認した。
- Terminalへ共通Snippet libraryを追加した。名前／説明／tag／`{{parameter}}`と`{{cwd}}`等のbuilt-inを登録し、複数Snippetを順序付きでcomposeできる。実行前Reviewで展開後command、byte数、許可root内の正規化済みworking directory、session conditionを確認する。Review後だけRun／Scheduleを有効にし、通常画面は共通`Snippets`入口とsession cardの3点menuだけに抑えた。
- 既定は対話Terminalへ入力しないDetached runとし、Web processの子processではなく`systemd-run --user` transient serviceで実行する。venv Python、固定環境、timeout／resource上限、2MiB bounded log、暗号化command snapshot、checksum、durable statusと監査を実装した。command／parameter本文はunit名、unit file、監査metadataへ含めない。
- 上級の`Send to session`は正確なsession IDへtmux bracketed pasteする。即時実行は明示Review後のAlwaysも選べるが、予約実行では無条件投入を拒否し、`Shell ready`または`Program matches`を必須にした。condition不一致は入力せず`SKIPPED`として残す。
- 1回／毎日／毎週／隔週、timezone、次回時刻、PC停止中のcatch-up、timeout、pause／resume／edit／delete／Run nowを持つscheduleを追加した。schedule別systemd user timerをatomicな0600 unitとして管理し、登録後はtimerのactive状態まで確認する。PCは右side panel、320pxはSafe Area対応bottom sheet、Library／Run／Schedulesの段階開示と状態indicatorにした。

検証: backend全368件、Terminal focused 27件、frontend TypeScript／production build成功。実Control Deck service上のPlaywright Chromiumで320pxのSnippet追加、parameter検出、Review前Schedule無効、Detached transient service実行と出力、隔週timer登録、session別入口、1280px右panel、両幅の横overflowなしを確認した。systemd journalでもvenv workerの成功とtimerのactive化、その後のtest cleanup停止を確認した。検証用Snippet／Schedule／run log／Terminal session／user／login session／auditを削除し、既存`490b7fd0`（PEDS／node）と`502a6ad9`（ControlDeck／node）は再起動・E2E後も不変。

## Terminal replay／mobile input — 再起動前checkpoint（2026-07-20 18:20 JST）

履歴記録。未解消項目と再開指示は上の19:18完了記録で置き換え済み。

- 解消・実機確認済み:
  - 画面swipeはgesture中のxterm local scrollだけに統一した。旧実装は`touchend`で同じ移動量をtmux `history_scroll`へ再送しており、指を離した瞬間の後着全画面描画で文字がずれていた。二重送信を削除後、320px実tmux E2Eで応答100ms未満、touchend後のbuffer／DOM行一致を確認した。
  - 右端overlay barも描画完了後のbuffer／DOM行一致を確認した。
  - Paste／Enter／矢印等のmobile補助buttonは`pointerdown.preventDefault()`でxterm textareaのfocusを奪わない。keyboardが開いている時／閉じている時の双方でfocus状態、terminal root／host／screen、Visual Viewport、page scroll位置が不変のE2Eに成功した。
- 未解消:
  - 初回接続と大量tmux履歴への再接続は、LIVE公開後に約16msだけ未完成画面を1frame表示し、その次に完成画面となる。最新320px E2Eは両方とも可視signature 2種類で失敗している。サーバー再読み込みE2Eは安全上、隔離環境以外では実行禁止に変更したため未再検証。
  - 確定原因はxtermの端末自動応答。最新診断では`history_end`受信611.7ms直後に、復元中queueへ溜まった7／11／25／25 byteの制御応答を611.8〜611.9msでLIVE flushし、tmux全画面PTY frame 78／279／357 byteが613.2〜613.7msに到着している。この公開後handshake redrawが高速スクロールに見える。通常の履歴frameや単純なDOM renderer遅延ではない。
- 次の実装:
  1. `TerminalConnectionController.markLive()`が現在同時に行う「state=LIVE」「queuedInput flush」「renderer公開」を分割する。
  2. 復元中はユーザー操作不能なので、queued inputをxterm自動応答としてoverlay下で先にflushする。
  3. backendと明示的なpresentation handshake／PTY出力sequence境界を設け、その応答によるPTY writeとDOM／buffer行一致完了後だけLIVE化・overlay解除する。90〜600msの固定idle待ちへ戻さない。
  4. 初回接続、大量履歴再接続、隔離serviceでのserver reloadの3timelineを可視signature 1種類で通す。本文swipe、右端bar、補助keyの合格試験も同時回帰する。
- E2E安全境界:
  - 旧テストは閉じたtest sessionではなく一覧の`.last()`へ再接続し、afterEachが「現在選択中session」を削除していた。このためE2E userがsouten所有の`cdc502ad`と`a2302ae9`を誤削除したことを監査logで確認済み（tmux session自体は復元不能）。
  - 修正後は作成前後のsession ID差分を確認して`ownedSessions`へ記録し、接続・削除をそのIDだけにfail-closedで限定した。server reload試験は`CONTROL_DECK_E2E_ALLOW_SERVICE_RELOAD=1`かつ非E2E session 0件でなければskipする。
  - 停止時のユーザーsessionは`490b7fd0`（`/home/souten/PEDS`, node）と`502a6ad9`（`/home/souten/ControlDeck`, node）の2件。最新の安全化後E2E前後で同じ2IDが保持され、test session残留なしを確認した。これらを検証cleanupで削除しない。`502a6ad9`の履歴には旧テスト識別文字列が混入している。
- 現在の検証証拠: frontend production build成功、Playwright test list 24件読込成功、Terminal backend unit 19件成功。実機focused E2Eは補助key／本文swipe／右端barが成功、初回接続／大量履歴再接続が失敗。serviceは2026-07-20 18:10 JST反映版でactive。

## Terminal replay／mobile clipboard UX（旧記録・上のcheckpointで更新）

- frontendは初期表示と再接続の切断検知時点から、initial frame・reset・replay frameのwrite queue完了までxterm描画面を同期的に隠す。backendは隠れた状態でtmux attachのalternate-screen初期化を先に消費し、`history_reset`後の通常bufferへcapture履歴を復元する。xtermのwrite callbackはDOM rendererの描画完了より先に返るため、最終frame後はPTYの静止を待ち、bottomへ確定してrefreshした表示内容が3 animation frame連続で一致してから一度だけ復元面を表示する。これによりサーバー再読み込み時の空／部分描画による高速スクロールを見せず、同時に本文のscrollbackも失わない。
- モバイル補助barは頻用する`Enter`を常設し、PasteはClipboard APIからterminalへ直接送信するため中間paste欄を廃止した。Clipboard APIを利用できない非secure接続／権限拒否時だけOS keyboardの貼り付けへ案内する。Enter／矢印／Esc／Tab／Ctrl等の補助操作はxterm textareaへ再focusせず、software keyboardを開かない。
- Copyは削除せずgestureへ割り当てた。Pasteを28px以上上へswipeすると中間menuなしでCopy確認sheetを直接開き、選択範囲またはactive bufferをコピーできる。gesture後のsynthetic clickを抑止して誤Pasteを防ぎ、context menuと`ArrowUp`でも同じdialogを開ける代替経路、desktop headerの常設Copyを維持する。
- tmuxは固定画面を再描画するためxterm側scrollbackだけでは実出力を移動できない。本文の縦swipeを認証済みWebSocketの範囲付き`history_scroll` controlへ接続し、backendが固定argvのtmux copy-modeを最大100行ずつ動かす。次の本文tap／補助key／Paste時はcopy-modeを先に抜けて通常入力へ戻す。tmuxなしfallbackは従来のxterm local scrollを使う。

検証: Terminal backend unit 19件、frontend TypeScript／production build成功。実ControlDeck systemd user serviceをAPIから再読み込みし、Playwright Chromiumで320pxの直接Paste、Paste上swipeから直接Copy、swipe時の誤Pasteなし、Enter／矢印でtextarea非focus、実tmuxへ180行を出力した本文swipe、履歴表示後のEnterによるlive復帰、full page reload、server reload中のxterm非表示と履歴保持、1280pxのdesktop Copy／remountを確認した。server reloadは`requestAnimationFrame`ごとの表示timelineを記録し、約16ms周期で表示signature変化時のスクリーンショットを保存する回帰テストを追加した。修正前は復元解除後に空／部分描画と完全描画の2種類を再現し、修正後は完成済みの可視frame 1種類、可視状態でのDOM mutation 0件を確認した。対象E2E 5件成功。検証用terminal session、user、session、auditは削除済み。

## Terminal session identity／App Studio project deletion UX（2026-07-20）

- Terminal一覧をopaqueなsession ID中心から、foreground program、homeを`~`へ短縮した現在ディレクトリ、最終activity、tmux永続性を主情報とするcardへ再構成した。青はforeground programあり、緑はshell待機、赤は終了を表し、青のpulseは直近10秒にactivityがある場合だけに限定した。tmux clientの接続有無は別badgeにして「前景program」「最近の活動」「画面を開いている」を混同しない。PIDはAPIの診断情報には維持するが通常一覧へは表示しない。
- 接続中Terminalのheaderにもprogram／directoryと、Live／Reconnecting／ExitedのWebSocket状態、Running／Shell readyのworkload状態を併記した。接続中も3秒間隔でmetadataだけを更新し、terminal output本文を一覧判定へ流用しない。終了は常時buttonから3点メニューへ移し、主操作をConnect 1個へ整理した。
- App StudioのProject cardへ3点メニューを追加し、Deleteだけを破壊的な副操作として配置した。確認dialogはApplication Spec、生成Source、build履歴／成果物も削除対象であり取消不能と明示する。成功後は一覧を更新してtoastを出し、backendはactive buildを409で保護したうえでterminal build記録／成果物をcontainment確認して削除し、project削除を監査する。

検証: backend全351件、Terminal／Application Builder対象49件、frontend TypeScript／production build成功。実ControlDeck serviceを再起動しhealth 200、systemd user service activeを確認。Playwright Chromiumで、320pxのshell待機cardから`~/ControlDeck`の`sleep`実行へ変化するprogram／directory／pulse状態、接続中header、一覧復帰後の状態、1280pxを含む横overflowなしを確認した。App Studioは320pxでProjectの3点メニュー→Delete確認→一覧消去→API 404、toast、および1280px横overflowなしを確認した。対象E2E 2件成功。検証用Project、terminal session、user、session、auditは削除済み。

## Application Builder Phase E7／B2.5 Secret Injection／Bounded Side-effect Source（2026-07-20）

- Workflow compilerでSecret参照を実名から決定的な`SECRET_001…`へ変換し、生成source、manifest、README、監査へ実名・値を含めない境界を追加した。生成runtimeは`CONTROLDECK_SECRET_001…`からだけ値を読み、欠落、未宣言alias、64KiB超を停止し、最終JSONとfile write内容を値で再redactする。Secret参照はHTTP header／bodyだけへ許可し、literal credentialとURL／path／制御／出力での利用をblocking diagnosticにした。
- C# Console `controldeck.csharp-console/1.4.0`とASP.NET `controldeck.aspnet-api/1.0.0`の共通runtimeへ`http.request`と`file.read/write/exists/glob`を追加した。HTTPは固定HTTPSまたはloopback HTTP、method／header／body／response上限、redirect／cookie無効、credential query／header制約を持つ。fileはapplication-owned root相対、realpath containment、途中symlink拒否、2MiB write、4MiB append、atomic overwrite、bounded globを持つ。HTTPとwriteの監査は内容を含めず、origin／相対path、byte数、結果だけをrotation付きJSONLへ保存する。
- ASP.NETではSecretまたは副作用を含むWorkflowにAPI-key認証を必須とし、anonymous endpointへの公開を生成前に拒否する。manifestはopaque環境変数、side-effect区分、work／audit rootだけを記録する。App StudioのExportは`B2.5/E7`、project badgeは`E7`として表示し、生成処理自体はexecutor、network、subprocess、filesystem write、Secret解決を行わない。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統に維持した。

検証: backend全350件、Application Builder unit 32件、frontend production build成功。公式.NET SDK 8.0.423を既知サイズ216,832,058 byteとgzipで確認し、10-file Console／15-file ASP.NET／動的path containment fixtureをwarning-as-error buildしてwarning／error 0件、双方self-test成功。実ConsoleでSecret欠落exit 134、local HTTP responseと書込fileの値redaction、内容を含めないHTTP attempt／200とfile success監査、`..` escape／symlink traversalのexit 134拒否を確認した。実Kestrelでhealth 200、API keyなし／誤り401、正しいkey 200、response／file／auditへの値非露出を確認した。実ControlDeck serviceへ反映し、Playwright Chromiumの320／1280pxでunauthenticated source block、15-file E7 Preview、Secret実名非返却、opaque alias／side-effect／root manifest、横overflowなし、console／page errorなしを確認した。対象E2E 2件成功。検証用project／workflow／user／sessionを削除済み。次は隔離build。

## Workflow Test primary action UX（2026-07-20）

- Workflow Editorの頻用操作として`Test`を3点メニューだけに置かず、主操作`Run`の直前へ常時表示した。`Test`は公開版を変更しない下書き実行へ直接入り、`Run`は必要な保存・公開を経て実行する。両者の副作用差をtooltipとaccessible nameにも明記した。
- ツールバーの過密化を避けるため、`Test`はbeaker icon付きのoutline secondary action、`Run`は塗りのprimary actionとして優先度を分けた。320pxでも両操作を隠さず、戻る・Saveを含む操作領域を44px以上に統一し、モバイルの保存状態は省スペースなaccessible status dotへ変更した。
- 3点メニュー側は重複する`Preview & Test`を`Preflight Check`へ整理し、安全な実行前チェックを初期選択する低頻度の補助導線に限定した。頻用Testは1 stepへ短縮しつつ、実行しない検証も失っていない。

検証: frontend TypeScript build／production build成功。実ControlDeck serviceとPlaywright Chromiumで、320pxにおけるTest→RunのDOM／視覚順、両ボタンの常時表示、公開有無の説明、Testから下書きテストmodeが選択済みで開くこと、明示確認までexecutorを開始しないこと、320／390／768／1280px横overflowなし、既存3点メニュー経由の安全確認、Runの入力・公開・実行回帰を確認した。対象E2E 2件成功。検証用workflow／user／sessionを削除済み。

## Application Builder Phase E6 Typed API Query／Filter／Sort／Pagination（2026-07-20）

- Application SpecのQueryをEntity／同期APIの2 sourceへ拡張した。Entity Queryは最大20 filter、最大3 sort、offset paginationをtyped contractにし、field型ごとに許可operatorと値型をcompilerで検査する。API Queryはroute parameterなしの同期endpoint、固定JSON object input、dotted result pathを持ち、request JSON Schemaとresponse内のobject collectionを生成前に照合する。API側のfilter／sort／paginationはendpoint inputへ明示する設計とし、二重の取得条件やAPI collectionへの未対応mutationはblocking diagnosticで停止する。
- App StudioのQuery Editorをsourceごとの段階開示へ再構成した。Entityではfield／operator／typed value、sort direction、Previous／Nextの有無を編集でき、APIではendpoint、collection result path、固定request inputだけを示す。Query ID、共通limit／cache／auto-loadは一か所に保ち、無効な保存済み参照も`unavailable`としてround-tripする。Data TableはQuery側page sizeを正とし、Inspector側の重複設定を表示しない。
- `controldeck.aspnet-api/0.9.0`はEntity listへURL-encoded JSON filter／sortとlimit／offsetを送り、API Queryへ固定inputをPOSTしてresult pathを安全に辿る。生成SQLite runtimeはmetadataのfield whitelistだけからSQL columnを選び、値をすべてparameter bindingする。LIKE wildcardをescapeし、型／operator／件数／JSON sizeをserver側でも再検査する。sortはIDを末尾へ加えて決定的にし、不正条件は固定400とする。browser側はQuery ID＋offset単位のcache／pending、Previous／Next、固定loading／empty／errorを扱い、row値は`textContent`だけへ描画する。

検証: backend全348件、Application Builder unit 30件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423で21-file生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright ChromiumでEntityのboolean filter、rank降順、2件offset pagination、同期API固定input／nested collection、未知field filterの400拒否、意図的503の固定error、XSS文字列非HTML化、320／1280px横overflowなし、鍵file非生成を確認した。実ControlDeck serviceのPhase A／focused workspace E2Eでfilter／sort／paginationとAPI Queryの編集、workspace移動後の未保存値保持、単一Save、21-file E6 source preview、catalog v11、generator 0.9.0、query runtime manifestを確認し、検証用project／workflow／user／session／auditを削除した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はSecret injectionとside-effect nodeの安全な生成境界、その後の隔離buildへ進む。

## Application Builder Phase E5 Typed Entity Query（2026-07-20）

- Application Specへ`queries`を追加し、Query ID、取得元Entity、1〜100件のlimit、自動取得、`network-only`／page memory cache、0〜3,600秒の鮮度をtyped contractにした。取得元Entityの存在とCRUD `list`公開、Query ID重複、binding参照、Data Table以外のconsumer、存在しないcolumnをcompilerで拒否する。既存`entity:` bindingは後方互換として維持する。
- App StudioのData画面へQuery Editorを追加した。CRUD listを公開済みのEntityだけを選択肢にし、通常はQuery ID、取得元、件数、自動取得、cacheだけを編集する。Query IDはbindingを壊さないstable IDとして作成後read-onlyにし、Data Table Inspectorでは自由入力ではなく保存済みQueryを選択する。Query binding時は無効になるTable側page sizeを隠し、Query側のlimitを単一の設定元にした。
- `controldeck.aspnet-api/0.8.0`は`query:`へ結び付いたData TableをEntity list routeへ生成する。初期状態をloadingまたは明示的な未取得として示し、empty、内部response本文を出さない固定error、44px Refreshでの再試行を同じlive statusに表示する。memory cacheはQuery ID単位で鮮度を検査し、同時取得を共有する。手動Refreshとcreate／update／delete後はcacheを破棄して再取得する。row値は引き続き`textContent`だけを使い、localStorageや任意scriptへ保存しない。

検証: backend全347件、Application Builder unit 29件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423で生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright Chromiumで意図的な503から固定error表示、Refreshによるempty回復、認証付きEntity create後のrow再取得、HTMLとして解釈しないXSS文字列、320／1280px横overflowなし、鍵file非生成を確認した。実ControlDeck serviceのfocused workspace E2EでQuery編集、workspace移動後の未保存値保持、単一Save、21-file E5 source preview、catalog v10、generator 0.8.0、query runtime manifestを確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はAPI query sourceとfilter／sort／paginationのtyped contractを定義する。

## Application Builder Phase E4 Typed Client State／Focused Workspace UX（2026-07-20）

- AppBuilderを長い設定一覧から、目的別の`Create`／`Target`／`Export`／`Review` workspaceへ再構成した。作成画面は`Canvas`／`Data`へ分け、PCではAdd／Canvas／Inspectorの3ペイン、320pxではCanvasを主面としてAdd／Layers／Inspectを下部ボタンとボトムシートへ移す。下書きを保持したままworkspaceを移動でき、保存は上部の単一`Save changes`へ統一した。詳細なbinding／interaction／JSON／AI lockは段階開示し、通常編集時の判断量を減らした。
- Application Specへtyped client stateを追加した。stateは安定ID、string／number／integer／boolean／object／array型、nullable、初期値を持ち、個別64KiB／合計256KiB、有限数、ID重複、初期値型をcompilerで検査する。App StudioのData画面で同じSpec／Undo／Redo／dirty stateへ統合して編集し、component bindingと`state-set` eventは存在するstateだけを選択できる。
- `controldeck.aspnet-api/0.7.0`はText／Markdown／Metric／Text Inputの対応型だけを`state:` consumerとして生成する。初期値はscriptへ直挿入せずescaped JSON data attributeからmemory mapへ読み込み、表示更新は`textContent`またはinput valueだけに限定した。Text Inputのchange、Workflow成功response、固定化したHTTP／network errorをtyped stateへ設定し、reloadでSpec初期値へ戻す。型不一致、consumerなしのstate-set、response schema不一致は生成前のblocking diagnosticとする。localStorage／永続DB／任意scriptは使わない。

検証: backend全346件、Application Builder unit 28件、frontend production build、生成JavaScriptのNode構文検査に成功。公式.NET SDK 8.0.423で生成Web／self-testをwarning-as-error buildしwarning／error 0件、self-test成功。実KestrelとPlaywright Chromiumで320pxのtyped初期表示、inputから複数consumerへの更新、HTMLとして解釈しないXSS文字列、Workflow成功object state、安全な500 error state、localStorage非使用、reload reset、横overflowなしを確認した。実ControlDeck serviceへ反映し、AppBuilder全体回帰とfocused workspace専用E2Eを320／1280pxで実行し、単一Save、モバイルbottom sheet、下書き保持、20-file E4 source preview、catalog v9、generator 0.7.0、console／page errorなしを確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped query bindingを定義し、取得状態／再読込／失敗表示の境界を実装する。

## Application Builder Phase E3 Workflow Form／Typed Result／Navigation（2026-07-20）

- Design System catalog v8で`action.workflow-run`へ保存済みWorkflow binding、同期API endpoint ID、result labelをtyped propertyとして追加した。App Studio InspectorではWorkflow bindingと、そのWorkflowへ結び付いた同期endpointだけを候補表示し、Navigate eventのtargetも実在Pageから選択する。endpoint ID省略時は同じWorkflowのendpointがちょうど1件の場合だけ自動解決し、binding／endpoint欠落、不一致、複数候補、async、route parameter、50 field超、未対応schema typeは生成前のblocking diagnosticにする。
- `controldeck.aspnet-api/0.6.0`は同期endpointのJSON Schema objectからstring／enum／integer／number／boolean／object／array formを生成し、required、文字数、数値範囲、説明をHTML controlへ写す。JSON Schemaのrequired booleanはfalseも正しく送れるようcheckboxのHTML requiredとは分離し、object／arrayは型を確認してJSON parseする。送信は既存HttpOnly session＋same-origin custom headerを使い、server側C2 JSON Schema検証を最終境界として維持する。
- 成功responseはprimitive、object、object-arrayを`textContent`だけでDOMへ構築し、object 1,000 field、array 1,000 row／20 columnで描画上限を固定した。`innerHTML`やSpec由来JavaScriptは生成しない。`success`／`error`のNavigate eventはcompilerで存在確認済みPageを固定routeへ変換し、HTTP／network failureをerror側へ送る。typed stateの宣言・consumerがない`state-set`や再帰Workflow eventは動作したふりをせずblocking diagnosticのまま残す。

検証: backend全344件、Application Builder unit 26件、frontend production build成功、生成JavaScriptのNode構文検査成功。公式.NET SDK 8.0.423を完全サイズ216,832,058 byte／公式Content-MD5／gzipで確認し、生成Web／self-testをwarning-as-error buildしてwarning／error 0件、self-test成功。実Kestrelで未認証401、session、CSRF headerなし401、request schema 400、required boolean=falseを含むtyped request／result、CRUD、CSP／nosniff／no-referrer、鍵file非生成を確認した。Playwright Chromiumで320pxのstring／enum／integer／number／boolean／object／array入力、typed result、320／1280px CRUD、success→Done／response schema 500→Errorsの固定navigation、横overflowなしを確認した。実ControlDeckへ反映し、認証付きE2EでWorkflow／同期endpoint選択、result label、catalog v8、21-file E3 source preview／ZIP、既存Entity／ASP.NET API／Console生成回帰、320／390／768／1280px overflowなしを確認した。検証用project、workflow、user、session、auditを削除済み。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はtyped client state／query bindingを先に定義し、そのconsumerがあるeventだけを安全に生成する。

## Application Builder Phase E2 Entity Mutation／Browser Session（2026-07-20）

- Design System catalog v7でData Tableへ`enableCreate`／`enableUpdate`／`enableDelete`をtyped boolean propertyとして追加し、既存Inspector、共通Application Spec state、50段Undo／Redo、単一Saveへ統合した。各mutationはbinding先Entityが同じCRUD operationを明示公開する場合だけ生成し、未公開operation／Entity collectionなしはblocking diagnostic、認証なしmutationはwarningにする。
- `controldeck.aspnet-api/0.5.0`はEntity schemaからcreate／update formとrowのMore menuを生成する。string／integer／number／boolean／offset datetime／JSON、nullable／default／maxLengthを型別controlとserializerへ写し、POST／PATCH／DELETE後にlistを再読込する。Deleteだけ確認し、赤色はDeleteだけに限定する。Entity値は引き続き`textContent`だけで描画し、任意HTML／JavaScript／`innerHTML`は生成しない。
- Page＋`authentication: api-key`では、環境変数のAPI keyを初回sign-in時だけ固定時間比較し、32-byte乱数tokenのSHA-256だけをprocess内に最大1,000件／12時間保持するHttpOnly／SameSite=Strict cookie sessionへ交換する。API keyやtokenをlocalStorage、成果物、log、SQLiteへ保存しない。unsafe methodはsession cookieに加えて`X-Requested-With: GeneratedApp`を必須にし、loginはIP単位5回／5分、source数10,000、body 16KiBへ制限する。平文HTTP loginはloopback clientだけを許可し、network公開時はHTTPSを要求する。
- GUI hostへCSP、`frame-ancestors 'none'`、nosniff、no-referrerを追加した。Antiforgery／data-protection keyはprocess内repositoryだけに置き、user profileへ鍵fileを残さない。sessionは意図的に再起動で失効し、永続認証や利用者管理を装わない。

検証: backend全343件、Application Builder unit 25件、frontend production build成功。公式.NET SDK 8.0.423の完全サイズ／公式Content-MD5／gzipを確認し、生成Web／self-testを`--warnaserror` buildしてwarning／error 0件、self-test成功。実Kestrelで未認証401、cookie属性、CSRF headerなしmutation 401、create／list／update／delete、logout、6回目login 429、再起動後session 401、CSP header、user profile鍵file非生成を確認した。Playwright ChromiumでAPI-key sign-in、HttpOnly非露出、320／1280px CRUD、Delete確認、横overflowなし、console／page errorなしを確認した。実ControlDeckへ反映し、認証付き320px E2Eでcatalog v7の3 mutation設定、Preview反映、API-key GUIの21-file E2 source preview／ZIP、既存D2／ASP.NET Entity／Console生成回帰、320／390／768／1280px overflowなしを確認した。検証用project、workflow、user、session、auditを削除済み。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はE3でWorkflow trigger form／result renderingとnavigation eventの安全な生成境界へ進む。

## Application Builder Phase E1 Blazor GUI Source（2026-07-20）

- `controldeck.aspnet-api/0.4.0`へSemantic Component treeの決定的Blazor static SSR生成を追加した。Page route／navigation、Stack／Row／Grid／Card、Text／Markdown／Metric／Text Input、Entity collectionへbindingしたData TableをRazor／CSS／JavaScriptへ変換し、GUI＋Entity時はmanifestを含む21-file ZIPを生成する。HTML／Razor attributeをescapeし、Entity rowは`textContent`だけで描画して`innerHTML`や任意codeを生成しない。
- Data TableはD1 CRUDのlist operationと同じbase pathへ接続し、Entity field binding、未公開list、未対応component、event／Workflow actionは生成前diagnosticで停止する。browser session認証adapterは未実装のため、Pageを持つ生成GUIは明示的な`authentication: none`だけを許可し、API key GUIを公開済みと装わない。
- 320px mobile、768px tablet、1024px以上desktopのresponsive Grid、horizontal table scroll、44px control、Safe Area、dark／lightを生成CSSへ固定した。Razor antiforgeryがuser profileへ鍵fileを暗黙生成しないよう、process内だけの`IXmlRepository`を生成してdata-protection keyを再起動時に破棄する。現段階のGUIはstatic SSR＋read-only Entity listであり、form actionや長期sessionを保証しない。

検証: backend全342件、Application Builder unit 24件、frontend production build成功。公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、self-test成功。実KestrelでPage／CSS／JavaScript、anonymous Entity create／list、SQLite永続化を確認し、Playwright Chromiumで320／1280pxのEntity row表示、横overflowなし、Safe Area rule、console／page errorなしを確認した。生成時に検出した一時data-protection鍵は削除し、最終生成物ではuser profileへの鍵file非生成も確認した。Linux／Windowsの生成選択肢はC# Console／ASP.NET Coreの2系統を維持する。次はE2でform／CRUD mutationと認証adapterの境界を設計する。

## Application Builder Phase D2 Entity Editor／CRUD Table Binding（2026-07-20）

- App StudioへresponsiveなEntity editorを追加し、Entity／table、field ID／type／nullable／default／maxLength／unique／index、selfを含むEntity ID relation／delete policy、CRUD有効化／operation／base pathをtyped controlで編集可能にした。最初のEntity追加時はdatabaseをSQLiteへ明示し、予約ID、重複ID／table／route、空field／operation、default／relation不整合は保存ボタンを無効にしてbackend diagnostic前にも示す。Entity定義削除だけは確認を要求する。
- Entity editorを独立snapshot保存にはせず、既存Design editorと同じApplication Spec state、50段Undo／Redo、dirty判定、単一Saveへ統合した。E2Eで検出した「Entity保存が未保存Designを上書きし得る」競合をこの統合で解消し、Design／EntityのどちらのSaveからでも同一snapshotだけを保存する。
- Component inspectorのEntity bindingは自由入力から、現在のEntityとgenerator管理列を含むfield候補のselectへ昇格した。`entity:Project`はcollection、`entity:Project.name`はfield bindingとして保存し、backendも存在しないEntity／fieldを`BINDING_ENTITY_MISSING`／`BINDING_ENTITY_FIELD_MISSING`で拒否する。これにより既存CRUD Table／Data TableをD1 CRUD schemaへ安全に結び付けられる。

検証: backend全341件、Application Builder unit 23件、frontend production build成功。binding Entity／field参照のvalid／invalidをunit回帰した。実ControlDeck serviceへ反映し、認証付き320px E2EでDesignと同じsnapshotへのEntity追加、Data Tableの`Project.name` binding、保存／再読込、field追加、SQLite Entity入りASP.NET 16-file ZIP、Console 10-file ZIP、Linux／Windows向け2生成選択肢、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、audit、Playwright artifactは削除済み。次はPhase E1でSemantic Component／Entity bindingをASP.NET Blazor GUI sourceへ変換する境界を実装する。

## Application Builder Phase D1 Typed Entity／SQLite Migration／CRUD Source（2026-07-20）

- Application Specの`entities`を任意dictからtyped contractへ昇格した。Entityは安全なID／table名、最大100 field、string／integer／number／boolean／offset付きdatetime／JSON、nullable、明示default、string長、unique／index、Entity ID外部キーとrestrict／cascade／set-null、明示的に有効化するCRUD operation／base pathを持つ。generator管理の`id/createdAt/updatedAt`、重複field／table／route、default型・長さ、参照先・型・nullability、SQLite以外のdatabaseを保存前diagnosticで拒否する。
- `controldeck.aspnet-api/0.3.0`はEntityを含む時だけ固定`Microsoft.Data.Sqlite/8.0.29`を追加し、`Entities.generated.cs`を含む決定的16-file ZIPを生成する。EntityなしのC2機能は追加packageなしの15-fileを維持する。Linux／Windowsでsource生成可能な選択肢はC# ConsoleとASP.NET Coreの2系統に限定し、advisor-only targetを生成可能と表示しない。
- SQLiteは`CONTROLDECK_APP_DATA_DIR`またはアプリ専用LocalApplicationData rootを`GetFullPath`で正規化し、固定`application.sqlite3`だけをWAL／foreign key有効で開く。startup migrationはtransaction内でtable、追加列、unique／通常index、外部キーを決定的に適用し、checksum＋Entity IDを記録する。既存列の型／nullability変更、後付けrelation、defaultなしrequired列追加はsilent変換せずstartupを停止する。
- 認証方針を継承するparameterized CRUDはlist（limit 1〜100／offset）、read、create、partial update、deleteをoperation単位で公開する。bodyは2MiB、100診断、unknown／管理列、型、maxLength、offsetなしdatetimeを拒否し、UUID path、unique／FK競合を400／409へ分離する。deleteは同じSQLite transactionの`_controldeck_audit`へaction／Entity／resource ID／時刻だけを永続化し、入力値やSecretをlogへ出さない。OpenAPI 3.1も実際に公開したoperationとschema／認証だけを出力する。

検証: backend全341件、Application Builder unit 23件、frontend production build成功。Entity有無の15／16-file決定性、typed validation、package／DDL／parameter、OpenAPI CRUDを回帰した。公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、self-test成功。実Kestrelで未認証401、入力400、create／list／read／patch／delete、default、unique 409、FK 409、cascade delete、UUID 400、WAL、追加列default migration、永続delete auditを確認し、既存boolean列をstringへ変える非互換migrationがstartupを明示停止することも確認した。実ControlDeck serviceへ反映し、認証付き320px E2EでSQLite Entityを含むASP.NET 16-file ZIP、Console 10-file回帰、Linux向け2生成選択肢、320／390／768／1280 overflowなし、console／page errorなしを確認した。次はPhase D2でEntity／relation／CRUD公開範囲を編集するGUIとCRUD Table bindingを実装する。

## Application Builder Phase C2 Schema／Durable Background Job Runtime（2026-07-20）

- `controldeck.aspnet-api/0.2.0`へ更新し、固定metadataの15-file ZIPへdependency-freeな`JsonSchema.generated.cs`と`BackgroundJobs.generated.cs`を追加した。manifest phase、capability、previewのblocking phaseもC2へ同期し、C# ConsoleはB2のまま分離する。
- API requestはroute値を注入する前のJSON body、responseはsync結果とasync完了結果を実際にschema検査する。OpenAPI 3.1にも同じschemaを出力し、request違反は400、response違反は値を露出せず500／failedへする。型、enum／const、object properties／required／additionalProperties、array items／unique／件数、string長、数値範囲／multipleOf、allOf／anyOf／oneOf／notを最大64深度・100診断・10,000 array itemで扱う。regex方言、format、`$ref`等の未対応keywordは保存／生成前のstructured diagnosticで停止する。
- chunked bodyを含めて最大2MiBをbufferし、空body、JSON object、Content-Type、不正JSONを区別する。API keyは引き続き環境変数だけから固定時間比較し、asyncのcancel理由をmanual、timeout、Application shutdownで分離した。例外値を公開／記録せず、内部logはjob IDと例外型だけに限定する。
- background job contractへIANA time zone、固定input、`skip/queue-one` concurrency、`skip/run-once` catch-upを追加した。manual jobは認証付き`/api/background-jobs/{definitionId}/run`、定義一覧、共通status／SSE／cancelを持つ。interval、daily、数値／月・曜日名／list／range／stepを扱う5-field cronはASP.NET `BackgroundService`で実行し、ブラウザやControlDeck Web processの子processにしない。
- schedule stateは`CONTROLDECK_APP_DATA_DIR`またはアプリ専用LocalApplicationData rootを`GetFullPath`で正規化し、その直下の固定ファイルだけへ1MiB上限でatomic move保存する。last start／evaluation、実行中pending、queue-oneを永続化し、再起動重複を抑止する。実行中crashはpendingを1回再実行し、DST invalid daily時刻は誤ったoffsetで実行せず、cronはUTC minuteを各time zoneへ変換して評価する。

検証: backend全340件、Application Builder unit 22件、frontend production build成功。同一入力ZIP、schema keyword、daily／cron／time zone、Workflow参照、予約route、15-file preview／downloadを回帰した。一時配置した公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、warning／error 0件、schema／wrapped cron self-test成功。実Kestrelで未認証401、request schema 400、正常response、response schema 500、manual／interval完了、manual overlap 409、cancel理由、OpenAPI、state保存、60秒interval再起動重複なし、実行中強制終了後のpending 1回復旧を確認した。実ControlDeck serviceへ反映し、認証付き320px E2Eでsupported schema＋manual jobを含むASP.NET 15-file ZIP、Console 10-file回帰、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、audit、一時SDK／生成物は削除済み。次はPhase D1でtyped Entity／SQLite migration／CRUD contractを定義し、API runtimeとGUI生成の共通永続化境界を実装する。

## Application Builder Phase C1 ASP.NET API Source Generator（2026-07-19）

- ASP.NET source generatorへ任意handler codeや曖昧なdictを渡さないため、Application Specへframework非依存のtyped `apiEndpoints`／`backgroundJobs` schemaを追加した。APIはstable ID、POST path（固定segment／`{parameter}`）、Workflow binding、sync／async、inherit／anonymous認証、request／response schema、0.1〜7200秒timeoutを持つ。JobはWorkflow binding、manual／interval／daily／cron、schedule、enabled、timeoutを持つ。
- validatorへ正規化したmethod＋route重複、Workflow binding参照、path parameter重複、明示anonymous公開warning、job Workflow参照、非manual schedule必須、interval秒数検査を追加した。生成時も予約route、100 endpoint上限、単一Workflow snapshotを検査する。local認証adapter、GUI Page、Entity、scheduled job、runtime JSON Schema validationは対応したふりをせずblocking diagnosticにする。
- `controldeck.aspnet-api/0.1.0`を追加し、`aspnet-blazor` targetのsource capabilityをC1 API範囲でavailableにした。同じ入力から固定metadataの13-file ZIP、net8.0 Web／self-test project、Dockerfile、README、checksum manifestを決定的に生成する。Blazor GUI生成はD/E〜G2、`aspnet-react`は未対応のまま分離する。
- 生成Web hostは2MiB request上限、`/healthz`、OpenAPI 3.1、route parameterを入力へ統合するsync／async POST endpointを持つ。API keyは成果物へ埋め込まず`CONTROLDECK_APP_API_KEY`から受け、`X-API-Key`を固定時間比較する。endpoint単位のanonymous指定をOpenAPIと実行時の双方へ反映する。
- asyncは最大1,000件のin-memory job、status、SSE、DELETE cancel、endpoint timeout、Application shutdown cancelを生成する。job作成時の認証方針をstatus／SSE／cancelへ引き継ぎ、終了済みcancelとの競合も409へ閉じる。scheduled background jobと永続queueは次のC2境界とする。

検証: backend全340件、Application Builder unit 22件、frontend production build成功。同一入力ZIP一致、認証／予約route／GUI・schema・schedule block、source preview／download／監査を回帰した。一時配置した公式.NET SDK 8.0.423で生成Web／self-testを`--warnaserror` buildし、双方warning／error 0件、self-test成功。実Kestrelでhealth、OpenAPI、未認証401、認証付きasync作成、DELETE cancel、`cancelled` status／SSEを確認し、一時SDK・生成物・serverを削除した。実ControlDeck serviceへ反映し、認証付き320px E2EでASP.NET 13-file preview／ZIP、Console 10-file回帰、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。次はPhase C2のscheduled background job／永続実行境界とruntime JSON Schema validationを設計・実装する。

## Application Builder Phase B2.4 Nested Loop Runtime（2026-07-19）

- C# Console generatorを1.3.0へ更新し、`control.loop`の`count`／`foreach`、`body`／`done` edgeをnative source対象へ追加した。loop自体は既存engine同様に通常nodeのretry／timeout permitを占有せず、各反復が独立したnested DAGを実行する。
- countは1〜100へclampし、foreachはJSON array／単一JSON値／非空行listを受け付けて最大100件とする。`parallel`は1〜5のbatch実行とし、body nodeは既存generated runtimeの全体4並列Semaphoreを共有するため、loop同士や通常nodeとの過剰並列を防ぐ。
- 各反復は親node outcomeとnamed variableのsnapshotを持ち、`{{loop.index}}`、`{{loop.item}}`、`{{loop.total}}`を公開する。反復結果は`index/item/outputs`としてdefinition順と独立した入力順で集約し、完了後は既存engine互換で最終反復のnode output／named variableだけをdone側contextへ引き継ぐ。
- loop完了時はbody edgeをouter schedulerへ再送せず、done／無指定edgeだけをliveにする。nested loop、body内condition／merge／retry／timeout、stop時の全体cancelは同じrecursive graph runnerを利用する。未知mode、非整数count／parallel、loop以外をsourceとするbody／done branchは生成前diagnosticで停止する。
- generated workflow sourceへ`#nullable enable`を追加し、C# `net8.0`のnullable annotationをwarningなく有効にした。

検証: backend全337件、Application Builder unit 19件、frontend production build成功。一時配置した公式.NET SDK 8.0.423でgenerated loop projectを`--warnaserror` buildし、warning／error 0件、foreach 2並列の実CLI結果`{"answer":"1:b/2"}`、generated self-test成功を確認後、一時SDKと展開物を削除した。実ControlDeck serviceへ反映し、認証付き320px E2Eでbranch／merge／data.template／count loop body・doneを含むWorkflowのPreflight ready、10 file Preview、1.3 source ZIP download、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。次はPhase C1のASP.NET API／health／OpenAPI generator基盤へ進む。

## Application Builder Phase B2.3 Named Variable／Pure Data Runtime（2026-07-19）

- C# Console generatorを1.2.0へ更新し、成功nodeの`config.output_var`をnamed output variableとして保存するgenerated runtimeを追加した。`{{vars.name.path}}`はnode ID参照と同じdot path／JSON array indexで解決し、依存nodeの完了後、後続nodeをscheduleする前に公開する。
- `data.transform`の`json_parse/json_get/json_set`、`data.template`のtext／JSON出力、`data.filter`のexists／truthy／equals／not_equals／contains／数値比較・unique・stable sort・limit、`data.aggregate`のcount／sum／avg／min／max・groupをnative source対象へ追加した。既存executorと同じUTF-8 2MiB、array 10,000件、number型、dot path上限契約を生成sourceへ保持する。
- templateは通常node、named variable、`data`疑似contextを共通の非再帰reference resolverで扱う。filterのJSON同値比較、object key／array contains、canonical unique key、型順sortと、aggregateの入力順groupを追加依存なしの`System.Text.Json`で決定的に生成する。
- JSON Schema validation、CSV相互変換、未知filter／aggregate操作、未知template format／sort orderは、互換性のないstubへ置換せずconfig単位のblocking diagnosticにする。Secret、human approval、loop、side-effect node、build／packageは引き続き未対応として明示停止する。

検証: backend全336件、Application Builder unit 18件、frontend production build成功。同一入力ZIP byte一致を維持し、named variable、全4 pure data node、2MiB／10,000件上限、unique／sort／group、非対応operation停止を生成sourceとdiagnosticで回帰した。実ControlDeck serviceへ反映し、認証付き320px E2Eでbranch／mergeのnamed variableから`data.template`を経由するWorkflowを作成し、Preflight ready、10 file Preview、1.2 source ZIP download、320／390／768／1280 overflowなし、console／page errorなしを確認した。検証用project、workflow、user、session、auditは削除済み。ホストに.NET SDKがないためgenerated sourceの実buildは未実施で、capabilityもbuild unavailableのまま。次はB2.4でloop／残りpure nodeのruntime境界を設計し、その後ASP.NET generatorへ進む。

## Application Builder Phase B2.2 Branch／Merge／Execution Policy Runtime（2026-07-19）

- C# Console generatorを1.1.0へ更新し、`condition.if`と`control.merge`を正式native source対象へ追加した。conditionは`eq/ne/contains/gt/gte/lt/lte`、mergeは`wait_all/collect/first_success/first_complete/quorum`を既存engineと同じcontractで生成する。
- generated runtimeへDAG schedulerを追加した。triggerだけを開始点にし、最大4並列、最初のlive入力、`join=all`、dead-edge伝播、未選択branchのskip、definition順tie-breakを実装。conditionの無指定edgeはtrue、error／timeout route、timeout専用edgeがない場合のerror fallbackを既存engine semanticsに合わせた。
- nodeごとのretry count（最大5）、retry wait（最大300秒）、timeout（0.1〜7200秒）、`on_error=stop/continue/branch`を生成する。各attemptへlinked cancellationを作成し、timeout時にunderlying Taskもcancelしてからretryする。metered nodeはSemaphoreSlim、trigger／waitはunmeteredとする。
- Workflow IRのretry有効時の既定waitをengineと同じ5秒へ補正した。生成runtimeの既定timeoutは通常120秒、`util.wait` 3700秒。stop時は残りgenerated taskをcancelして待機し、browser／ControlDeck processから独立して終了する。
- generator preflightは対応済みbranch／execution policyのblockを解除し、不正branch、未知`on_error`、human approval、named output variableは引き続きblocking diagnosticにする。generator versionはcapability、manifest、generated metadataのすべてで1.1.0へ同期した。

検証: backend全335件、Application Builder unit 17件、frontend production build成功。true／false edge、wait-all merge、retry既定5秒、timeout、continue、4並列、error/timeout routeを生成source上で回帰し、同一入力ZIP byte一致とplaceholder collision防止も維持。実ControlDeck serviceへ反映し、認証付き320px E2Eでcondition→true/false→merge Workflowを作成し、Platform Advisor、Preflight ready、10 file Preview、1.1 source ZIP downloadまで確認した。F3.1〜F3.7、320／390／768／1280 overflow、console／page errorも同時回帰し、検証用project、workflow、user、session、auditは削除済み。次はB2.3のnamed variable／pure data node拡張。

## Application Builder Phase B2.1 Deterministic C# Console Source Generator（2026-07-19）

- `csharp-console`を最初の正式source targetとし、保存済みApplication SpecとWorkflow snapshotだけからC# `net8.0` projectを決定的に生成するbackend generatorを追加した。対応nodeは`trigger`、`util.wait`、制限付き`util.now`、`var.set`、制限付き`string.op`、`output.render`、`signal.display`。node実行やLLMによる自由code生成は行わない。
- Managed (`Generated/`、project file)、Extension (`Extensions/`)、Config (`appsettings.json`)を分離し、generator version、Spec／Workflow checksum、全file SHA-256、managed／extension／config一覧を`.controldeck/generation-manifest.json`へ記録する。ZIP entryはpath順、固定timestamp、固定permission、無圧縮として、同じ入力から同じbyte列とarchive checksumを生成する。
- source projectへCLI entrypointとNuGet追加依存のないgenerated self-test projectを含めた。app identifierはC# identifierへ再sanitizeし、ZIP pathを生成器管理下に固定する。source生成はメモリ内だけで、filesystem write、subprocess、network、executor、Secret解決を行わない。
- `GET /application-projects/{id}/source-preview`でfile一覧、checksum、manifest、diagnosticを副作用なし確認し、`POST /application-projects/{id}/source-archive`で認証・edit権限・CSRFを通してZIPを取得する。ZIP生成はtarget、generator、checksum、file数、byte数だけを監査し、source本文やSecretを監査logへ保存しない。
- Secret参照、複数Workflow、未対応node、branch edge、retry／timeout／continue policy、未対応`string.op`／日時formatはblocking diagnosticにする。`csharp-console`のsource matrixだけをavailableへ変更し、build、package、signingは未実装のまま成功扱いしない。
- App Studioへ保存済みConsole target用のSource Preview、Managed／Extension／Config file一覧、4種checksum、Source ZIP生成を追加した。Platform Advisorでlinux targetへ切替、Preflight ready、target保存、Preview、Downloadの順に進み、未対応Specでは生成を無効化してbackend diagnosticを表示する。

検証: backend全334件、Application Builder unit 16件、frontend production build成功。同一入力のZIP byte完全一致、固定entry metadata、manifest checksum、未対応node／Secret停止、認証API、2回の監査記録を確認。実ControlDeck serviceへ反映し、認証付き320px E2Eでweb向けB1 blockからlinux C# Console推薦へ切替、Preflight ready、target保存、10 file Preview、実ZIP downloadを確認した。F3.1〜F3.7、320／390／768／1280 overflow、console／page errorも同時回帰し、検証用project、workflow、user、session、auditは終了時に削除した。次はB2.2 branch／execution policy／native node拡張、その後ASP.NET generatorとbuild境界。

## Application Builder Phase B1 Platform Advisor／Preflight（2026-07-19）

- backendのframework registryを10候補のSDK、feature、対応platformと`spec/source/localBuild/remoteBuild/package/signing/store/stability` matrixへ拡張した。host SDKはread-onlyに検出し、Apple targetのmacOS host制約と未提供generatorを成功扱いしない。
- 対象OS、offline、local file、tray、background、GPU、embedded server、store、優先言語、native feel、web reuse、package sizeを固定registryだけで決定的に採点するPlatform Advisor APIを追加した。全候補の順位、理由、制約、代替を返し、同一入力は同一結果になる。
- App Studioへschema/capability駆動のPlatform Advisorを追加した。推薦を初期選択しつつ、複数frameworkのoverride、要求platformのcoverage確認、target保存を行える。未実装の生成／build操作は表示しない。
- 保存前Preflightは既存Spec validationとtarget matrixを統合し、framework、platform、SDK、host、source generatorのblocking diagnosticを返す。executor、network、subprocess、filesystem write、Secret解決はすべて行わず、その副作用なし状態もresponseで明示する。
- target validatorへplatform必須・framework対応platform検査を追加した。B1では全frameworkの`source`を`unavailable`に保ち、`SOURCE_GENERATOR_UNAVAILABLE`で明示停止する。次はB2のdeterministic source generator基盤。

検証: backend全332件、Application Builder unit 14件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2Eで決定的な`aspnet-blazor`推薦、ASP.NET Core + Blazor／React-PWAの2 target override、Preflightの生成block、保存後APIのtarget 2件を確認。既存F3.1〜F3.7を同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake generate/build/publish UIなし。検証用project、workflow、user、session、auditは終了時に削除した。

## App Studio F3.7 Accessibility Audit／Keyboard Reorder（2026-07-19）

- backend Design System catalogをv6へ更新し、通常文字contrast 4.5:1、大きな文字3.0:1、touch target 44px、focus indicator 2pxを監査閾値として配信。frontendへ閾値を直書きしない。
- EditorのAuditはDefault Previewの実DOMを走査し、annotateされた実表示文字のcomputed foreground／opaque background／継承opacityからWCAG contrastを計算する。semantic interactive controlは実bounding box、`tabIndex`／disabled状態、focus後のcomputed outline／box-shadowを検査し、contrast／focus／keyboard／touch別に件数とdiagnosticを表示する。
- Text Inputを副作用のないread-only focusable controlとしてPreviewし、Workflow action、Event付きTable／Chartへ44px以上、keyboard到達性、2px focus outlineを付与。AuditはDefault stateへ戻して実行し、Workflow、Event、navigation、network、DB、LLM、Secretを実行しない。
- accent preview色をwhite小文字との4.5:1以上へ調整し、blue／violet／emerald／amberの全presetで共通rendererが監査可能な配色にした。任意CSSや色値は引き続きSpecへ保存できない。
- Component Treeの各非root部品へ`Alt+ArrowUp`／`Alt+ArrowDown`と`aria-keyshortcuts`を追加。dragやtouch向けInspector Moveと同じ単一操作、Undo／Redo、dirty、Saveへ接続し、keyboardだけでも兄弟順を変更できる。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでTreeのcomputed 2px outline、Alt+↑／↓によるPreview DOM順の往復、Preview inputのcomputed 2px outlineと実寸44px以上、contrast／focus／keyboard／touch audit全passを確認。F3.1〜F3.6、保存／reload、Binding/Event、Parameterized Template、Visual Diff、3案比較、Patch部分適用、lockも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.6 Parameterized Composite／Pattern（2026-07-19）

- backend Design System catalogをv5へ更新し、5 Composite／4 Patternすべてへ型付きparameter schemaを追加。key、label、string／number／boolean／enum、default、required、文字数／数値範囲、選択肢、固定Component ID＋property targetをbackendだけで定義する。
- KPI、Job Status、Log Viewer、CRUD Table、Timeline、Dashboard、Settings、Wizard、Launcherで、title、accessible label、action label、help／empty text、初期数値等を挿入前に設定可能にした。既定値だけでも従来と同等の有効なtemplateを生成する。
- frontendはcatalogからparameter dialogを生成し、必須、型、範囲を挿入前に検査する。任意JSON Pointer、Binding、Event、式、HTML、code targetは受け付けず、catalogが宣言した既存Semantic Component propertyだけへ値を適用してからIDを衝突なく再採番する。
- parameter展開を既存の単一template挿入操作へ含め、Undo／Redo、dirty判定、明示Save、backend Spec validation、reload復元を維持する。parameter値やtemplate metadataをruntime実行せず、最終的な通常Component propertyだけをSpecへ保存する。
- focus／keyboard／contrastの自動auditはF3.7で実装した。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでKPI必須値のInsert停止、`CPU Load`／`42`、Dashboardのtitle／metric／chart／table labelを設定し、Preview反映、保存、reload後の表示・chart accessible name復元を確認。F3.1〜F3.5、Binding/Event、Visual Diff、3案比較、Patch部分適用、lockも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.5 Visual Preview Diff／3案比較（2026-07-19）

- EditorのSpec描画を共通`AppSpecPreview`へ分離し、通常Editor、Patch Review、AI Design Proposalが同じSemantic Component、Design Token、Preview state、responsive Grid rendererを使用する。比較画面だけの別実装や静的mock画像を持たない。
- Patch ReviewへBefore／Afterの実画面比較を追加。選択中Patchをbackendで適用・再検証した`patchedSpec`だけをAfterへ渡し、Mobile／Tablet／Desktopを同じviewport条件で切り替える。選択変更時は従来どおり旧Previewと視覚比較を破棄し、再PreviewまでApplyを無効にする。
- AI DesignのSimple／Balanced／Dense各案へ実画面Previewを追加し、3案を同一viewportで並べて比較できる。各案のvalidity、diagnostic、理由、Patch件数、Review導線を維持し、自動適用はしない。
- 共通Previewは読み取り専用で、Workflow、Event、navigation、network、DB、LLM、Secret解決を実行しない。source generator／buildの未実装表示も維持する。
- template parameterはF3.6で実装し、focus／keyboard／contrastの自動auditは後続とする。

検証: backend全331件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでPatch Beforeに`Hello App Studio`、Afterに`Patched App Studio`を同時表示し、Mobile→Desktop切替後に選択PatchだけをApplyできることを確認。schema準拠の決定的proposal応答でSimple／Balanced／Dense 3案のMobile PreviewとDesktop切替も確認した。F3.1〜F3.4、保存／reload、Binding/Event、lock拒否も同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.4 Structured Binding／Event Editor（2026-07-19）

- backend Semantic Component catalogをv4へ更新し、11種のBinding sourceをlabel／reference label付き定義として配信。input／workflow action／table／chartごとのeventと、Run workflow／Navigate／Set state actionの許可組合せ・target種別も同じcatalogを正とする。旧`bindingSources`は互換読取用に維持する。
- validatorはBindingのsource／reference、空値、長さ、Secret参照と、component固有event、許可action、target形式、Workflow／Page参照、State keyを保存・Patch・AI proposalの全経路で検証する。任意handler code、式、Secret、runtime実行は受け付けない。
- Inspectorへsource／referenceの構造化Binding editorと、event有効化／action／targetの構造化Event editorを追加。catalogからcontrolを生成し、既存`source:reference`文字列とobject形式を読み取り、保存時は後方互換な文字列へ正規化する。
- 全controlを44px touch targetとし、既存Undo／Redo、dirty判定、明示Saveへ接続。Eventの初期targetは決定的な安全値を使い、PreviewではWorkflow、navigation、network、DBを実行しない。
- 視覚diffはF3.5で実装し、template parameter、focus／keyboard／contrastの自動auditは後続とする。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでWorkflow output BindingとChange→Set state Eventを編集し、保存、reload復元を確認。F3.1〜F3.3、Patch部分適用、lock、AI Designも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.3 Structured Grid／Table／Chart Editor（2026-07-19）

- Responsive Gridのmobile／tablet／desktop列数、Data Tableのcolumn、Line Chartのseriesを専用property schemaへ昇格。frontendが独自の列型・tone・上限を持たずbackend catalogを正とする。
- backendでGrid 1〜12列、Table最大50列、Chart最大20 series、identifier、重複key、表示label、column type、semantic toneを検証。保存、Patch Review、AI proposal Previewの全経路に適用する。
- Inspectorへ3 breakpoint数値control、Table column／Chart seriesの追加・削除、key／label、type／tone editorを追加。全controlをmobileで44px以上とし、既存Undo／Redo、dirty判定、明示Saveへ接続した。
- Previewは選択中viewportの列数を実際のGridへ反映し、設定済みTable headerとChart series名を表示する。静的sampleだけを使い、Workflow、network、DB、Secretは実行しない。
- 自由formatter code／CSS色／式は受理しない。binding/event専用editorはF3.4で実装し、template parameter、視覚diff、focus／contrast auditは後続とする。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでGrid mobile 2列、Table column、Chart seriesを編集し、Preview反映、保存、reload復元を確認。F3.1/F3.2、Patch部分適用、lock、AI Designも同時回帰し、320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.2 Schema-driven Properties／全状態Preview（2026-07-19）

- backend Design System catalogをv3へ更新し、全11 Semantic Componentへstring／multiline／boolean／number／enum／JSONのproperty schema、required、選択肢、数値範囲を追加。保存・Patch・AI proposalの全経路で同じschemaを再検証する。
- Inspectorへschema駆動fieldを追加。単純項目はラベル付き44px controlで編集し、columns／series等と将来fieldは既存Properties JSONから編集できる。frontendにcomponent別formを二重定義しない。
- Previewへbackend catalog由来のDefault／Loading／Empty／Error／Disabled selectorを追加。同じSpecとMobile／Tablet／Desktop frameでskeleton、空データ、user-facing error、操作不可状態を副作用なしで確認できる。
- input／actionに加えてtable／chartのaccessible labelを検証し、chart role/label、loadingの`aria-busy`、status／alertをPreviewへ追加。Page title欠落は既存Specを拒否せずwarningにする。
- 状態Previewは設計時だけの表示でSpecへ保存せず、executor、network、DB、Secretを呼ばない。source generator、build、視覚diff、template parameterは未実装として維持する。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2Eでschema駆動Text編集、5 Preview state、Terminal preset、KPI Composite、Dashboard Pattern、保存／reload、Patch部分適用、lock拒否、AI Design入口を確認。320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## App Studio F3.1 Design System foundation（2026-07-19）

- backend schema catalogをv2へ更新し、14 Design Token群、Modern／Compact／Touch／Dashboard／Data Dense／Minimal／Terminal／Mediaの8 presetを追加。Application Specのpreset、token名、token値を同じregistryで検証し、未知値と任意CSSをblocking diagnosticにした。
- KPI Card／Job Status／Log Viewer／CRUD Table／Timelineの5 Composite、Dashboard／Settings／Wizard／Launcherの4 Patternを、既存Semantic Componentだけで構成した決定的templateとして配信する。
- EditorへDesign System selectorを追加。frontendへpreset／template treeを二重定義せず、catalogから適用・挿入し、既存Component Treeと衝突しないIDへ再採番する。操作は既存Undo／Redo、dirty判定、backend validation、明示Saveを通す。
- Previewへaccent、spacing、radius、surface、text、typography、shadow tokenを反映。input／actionの明示的な空labelはa11y errorとし、省略時はcatalog defaultを使って既存Spec互換を維持する。
- source generator、build、template parameter、全状態preview、視覚diffは未実装のまま成功扱いせず、F3.2以降の残件とした。

検証: backend全331件、Application Builder unit 13件、frontend production build成功。実ControlDeck serviceへ反映し、認証付き320px E2EでTerminal presetのmonospace反映、KPI CompositeとDashboard Patternの挿入、保存／reload復元、既存Patch部分適用・lock拒否・AI Design入口を確認。320／390／768／1280の横overflowなし、console／page errorなし、fake build UIなし。検証用user、session、auditは終了時に削除した。

## Workflow navigation／Run統合（2026-07-19）

- 独立した`Play` navigationを廃止し、sidebar、mobile navigation、Quick Actions、Command Paletteを`Workflows`へ統合。`workflows.edit`または`workflows.run`のどちらかを持つ利用者へ表示する共通navigation metadataにした。
- `Workflows`一覧はeditor利用者へdraftを含む編集対象、run-only利用者へ専用`/workflow-runner` API由来の公開済み項目だけを表示する。公開版は各項目の`Open App`から開き、内部定義を返さない既存の公開API境界を維持する。
- `/runner?workflow={id}`は公開アプリの互換deep linkとして残し、workflow未指定時は`/workflows`へ戻す。旧mobile navigation保存値の`/runner`は`/workflows`へ重複なく変換し、localStorageへ永続化する。
- Workflow Editorの主操作を`Run`へ変更。入力dialog、差分時だけの保存・公開、公開version実行、同じ画面のdebug panel表示を一操作にまとめた。公開だけの操作は`More > Publish Only`へ維持する。

検証: backend全330件、frontend production build成功。実ControlDeck serviceを`./deck.sh`で再起動し、health 200、systemd user serviceのenabled／activeを確認。認証付きPlaywright 8件で320pxのRun入力・実行・debug panel、公開アプリ実行／承認、旧navigation保存値移行、mobile設定、390px Safe Area、1280px公開実行、横overflowなしを確認した。検証用user、session、auditは終了時に削除した。

## App Studio F2.3 Structured AI Design Proposals（2026-07-19）

- App Studioへ`AI Design`を追加。要求、application／mobile／選択component scope、Preserve／Balanced／Redesign mode、検出済みmodelを指定し、Simple／Balanced／Denseの3案を生成する。
- 既存共通runtime providerを再利用し、Ollama／llama.cpp／LM Studio等の登録済みendpoint/modelだけを許可。llama.cppは停止時の自動起動・model load待機を継承する。
- LLMへはredact・文字数制限済みApplication Specとbackend catalogのSemantic Component／Design Token／Binding Sourceだけを送る。自由code、Secret、任意file、DB実データは送信しない。
- LLM応答を3案の説明と`add/remove/replace/move`へ限定。Patch値を`valueJson`としてschema拘束後、backendで再parseし、各案をF2.1 Previewへ通す。案の選択後もF2.2 Patch Reviewで部分選択・差分・lock・checksumを再確認する。
- Application Spec v1へ`llmRuntime`を追加。`None`と`External provider · not bundled`を編集でき、Ollama／LM Studio／OpenAI互換を選択可能。Externalでruntime同梱を指定するとblocking diagnosticとし、接続先/modelは環境変数で注入する。
- source generator、build、Embedded Runtime、Remote ControlDeck、視覚diff、案の合成は未実装として選択不可／非表示を維持する。

検証: backend全330件、Application Builder unit 12件成功、frontend production build成功。実Ollama `qwen3.6-27b-q5_k_m`で3案生成を実施し、3方向のschema parse、正式Patch変換、案ごとのPreview診断を確認（1案valid、2案は未知component／不正pathとして安全にApply不可）。認証付き320px E2EでExternal LM Studio非同梱設定、AI Design dialog、未入力時Generate停止、既存Patch／lock／overflow回帰を確認。

## App Studio F2.2 Patch Review／部分適用（2026-07-19）

- Application Editorへ`Review Patch`を追加。1〜200件の`add/remove/replace/move` JSON Patchを読み込み、operation単位で選択して正確なsubsetだけをbackend Previewへ送る。
- Before／AfterのPage・Component数、structured diagnostic、base/result checksumを表示。選択変更後は旧Previewを破棄し、再PreviewまでApplyを無効化する。
- 有効な選択差分だけをchecksum付きApply APIで原子的に保存し、Project queryを再取得してPreview／Tree／Inspectorへ反映する。dirtyなlocal Designがある間はReviewを停止し、先に明示Saveを要求する。
- Inspectorへstructure／binding／style／position／content lock編集を追加。保存したcontent lockによる表示値変更拒否をPreview上で確認でき、`PATCH_LOCK_VIOLATION`時はApplyできない。
- AI提案生成、視覚diff、3案比較は未実装。高度なstructured Patch importだけを提供し、fake AIやdummy build操作は追加していない。

検証: backend全328件、frontend production build成功。認証付きE2Eで320pxから2件のPatchを読み込み、1件だけを選択・Preview・Apply、非選択Component非追加、保存後のcontent lock設定、ロック対象Patch拒否、320／390／768／1280の横overflowなし、fake build UIなしを確認。実ControlDeck serviceへ反映してhealthを確認した。

## App Studio F2.1 Structured Patch foundation（2026-07-19）

- AI設計提案を自由codeではなくApplication Spec JSON Patchへ限定するbackend基盤を追加。`add/remove/replace/move`、最大200 operation、JSON Pointer深度64をschema化した。
- 副作用なしPreview APIでdeep copyへPatchを適用し、base/result checksum、patched Spec、適用済みoperation、structured diagnosticを返す。完成Specは既存のcomponent、binding、secret、target検証を再通過する。
- structure／binding／style／position／content lockをcomponentとancestorの双方で強制。lock変更、prototype pollution token、不正escape、範囲外index、Spec外scopeを拒否する。
- ProjectへのApply APIはbase checksumによる楽観排他を必須にし、stale差分を409で停止。全Patchが有効な場合だけatomic commitし、patch件数とchecksumを監査する。
- F2.2の比較・部分適用UIとF2.3のLLM 3案生成はこのAPIを利用する。現時点でfake AI提案UIは追加しない。

検証: backend全328件、Application Builder unit 10件成功。replace＋move、入力Spec不変、content lock、scope/prototype拒否、secret literal拒否、Preview checksum、atomic apply、reload済みSpec、stale checksum 409を確認。

## App Studio F1.2 Component Editor（2026-07-19）

- backend Semantic Component catalogから生成するPalette、再帰Component Tree、Preview選択、Binding／Properties InspectorをApplication Editorへ統合。frontendへ部品一覧を二重定義しない。
- Page＋responsive Stackの初期作成、選択containerへの部品追加、primitive選択時のroot追加、上下移動、削除、Desktop dragによるcontainer変更を実装。touch／keyboardでは44px以上の明示buttonを使える。
- Desktop／Tablet／320px Mobile Previewを同じApplication Specから描画。Stack、Row、Grid、Card、Text、Markdown、Metric、Text Input、Workflow Run、Table、Line Chartをstatic sampleで確認できる。
- 最大50操作のUndo／Redo、dirty判定、backend validationを通す明示Save、保存後reload復元、接続Workflowへの導線を追加。static previewはexecutor、network、DB、LLM、secretを実行しない。
- source生成／build／packageのdummy操作は追加していない。

検証: backend全325件、frontend production build成功。認証付きE2Eで320pxからWorkflow→App Studio Project作成、Page／Text追加、Properties編集、Undo／Redo、Mobile Preview、Save、reload復元、320／390／768／1280の横overflowなし、fake build UIなしを確認。

## App Studio F1.1 Semantic Component foundation（2026-07-19）

- framework固有classを保存しないSemantic Component catalogをbackendへ追加。layout、display、input、action、table、chartの初期11部品と決定的default、container可否、Design Token enum、binding sourceをschema APIから配信する。
- Application Spec v1へ再帰component tree、Page root、structure／binding／style／position／content lockを後方互換で追加。root未定義の既存Pageは引き続きround-tripできる。
- 全Page横断のcomponent ID重複、unknown type、primitiveへのchildren、不正children型をstructured Diagnosticとして検出。frontend editorが独自の部品対応表を持たない境界を確立した。
- 本PRはF1 frontend editorの基盤に限定し、生成／build／dummy成功、LLM自由code生成は追加しない。

検証: backend全325件、Application Builder unit 7件成功。既存Spec互換、正常component tree、重複ID、unknown type、children制約、schema API catalogを確認。

## Mobile bottom navigation customization（2026-07-19）

- Settingsへ`Bottom Navigation`設定を追加。現在の権限と導入済みfeatureから利用可能な画面だけを候補にし、端末ごとに0〜6件を有効化できる。
- 選択項目は44px以上の上下buttonで安全に並べ替え、即時保存する。ResetでHome／Apps／Play／Terminal／AI Assistantの推奨構成へ戻せる。
- Moreは選択数に含めず常に右端へ固定。選択項目が少ない場合は各項目を最大96pxに抑え、6項目＋Moreでも320pxを横overflowしないflex配置とした。
- navigation metadataを共通catalogへ抽出し、desktop sidebar、mobile navigation、Settings候補のlabel／permission／feature判定を二重管理しない。

検証: backend全324件、frontend production build成功。認証付きPlaywright 3件で320×700から6項目選択、7列相当表示、上端への並べ替え、reload後の復元、7件目候補の無効化、More固定、横overflowなしを確認。

## iPhone standalone Play header境界修正（2026-07-19）

- 共通headerが`height: 48px`へ固定されたまま上部Safe Areaを内側へ加算していたため、ホーム画面から全画面起動したiPhoneではLogoがheader外へ押し出され、直後のPlay背景と重なっていた。
- headerを固定高からSafe Area込みで自然に伸びる`min-height`へ変更。Playだけへ例外余白を加えず、全通常ページでLogo、本文、下部navigationの領域境界を維持する。
- Safe AreaをCSS変数境界にまとめ、実機相当47pxを自動テストで再現可能にした。
- 下部navigationだけでなくMoreのQuick ActionsからもAI Assistantを開ける導線を復元し、主要機能の入口を一貫させた。

検証: frontend production build成功。認証付きPlaywright 2件で通常15ページの320×700／1280×800統一レイアウトと、390×844・上部Safe Area 47pxでLogo下端よりPlay背景開始位置が下になることを確認。実ControlDeck serviceへ反映済み。

## Navigation naming／page layout統一（2026-07-19）

- グローバルnavigation、mobile navigation、Quick Actions、Command Palette、各機能menuを英語名へ統一。公開Workflowの実行面は短く幅を取らない`Play`、Application BuilderのUI製品名は将来の設計・編集機能を含む`App Studio`とした。
- 通常ページへ共通`PageHeader`を導入し、titleを20px／line-height 28px、同じ上端・説明・action配置へ統一。AI Chat、Workflow Editor、接続中Terminal、Remote Viewerは独立表示として維持する。
- mobile navigation itemへ`min-width:0`とtruncateを追加し、英語名が320px gridの列幅を押し広げないようにした。

検証: frontend production build成功。認証付きPlaywright 4件でApp Studio導線、Playの実行／承認、320×700と1280×800の通常15ページを確認し、全page titleが20px／28px、document/body横overflow 0となることを確認した。

## Remote Desktop service health（2026-07-19）

- Remote画面の状態確認をguacd単体から、ServerPC接続先のTCP待受確認まで拡張した。guacd／WebSocketが正常でもxrdpが停止している状態を区別し、接続を繰り返さずSSHで実行する復旧コマンドを表示する。
- 状態は10秒ごとに再確認し、xrdp復旧後は警告を自動解除する。秘密値は送信せず、保存済みself connectionのhost／portへの有限TCP接続だけを使う。
- 実機調査ではControlDeck→WebSocket→guacdは成功し、OSのxrdp／xrdp-sesmanがinactive、localhost:3389が閉じていたことを確認した。起動にはroot権限が必要なため、Webプロセスからsystem serviceを直接操作しない。

検証: remote backend test 9件、frontend production build成功。実機の停止状態でlocalhost:3389 unavailableと復旧案内を確認。

## Project Lab core（2026-07-19）

- `/project-lab`へCodeDEV成果物評価画面を追加。`~/CodeDEV`直下のproject、Python/Node/Vite/React/CMake/Rust/.NET/static-web、Git branch/dirty、manifest profileを設定なしで検出する。
- `.controldeck/project.json` v1をstrict Pydantic schema化。command文字列、project外cwd/glob、秘密environment直書きを拒否し、argv配列とSecret名参照だけを受け付ける。
- HTML、画像、CSV/TSV、JSON、Markdown、PDF、audio/video、log/textをread-only artifactとしてcatalog化。HTMLは認証付き配信、CSP、script無効sandbox iframe、その他は型別preview/downloadを使用する。
- CodeDEV外/symlink escape、秘密file名、`.env`、source、`.git`、`.venv`、node_modules、build cacheを除外。inline JSON/CSV/textの秘密らしい値をredactする。
- `project_lab.view`をadmin/operatorへ追加。現Phaseはdiscoveryとpreviewだけで、programの自動起動、run成功dummy UI、任意port proxy、LLM送信を実装しない。

検証: backend全313件、frontend production build成功。実ControlDeck serviceへ反映し、隔離した`~/CodeDEV/codex-project-lab-e2e`でHTML sandbox、redact済みJSON、CSV tableを操作確認。320×700、390×844、1280×800で横overflow 0、sandboxによる意図的script block以外のconsole/page error 0。検証project、user、session、auditは確認後に削除した。

## 公開アプリ（Workflow Runner）（2026-07-19）

- 公開済みWorkflowをキャンバスなしで操作する「公開アプリ」を`/runner`へ追加。公開版の入力form、
  想定output contract、副作用区分、実行、停止、human approval、typed output、最近の実行、過去入力再利用を同じ画面へ統合した。
- 専用`/workflow-runner` APIは公開名・説明・version・input/output schema・結果だけを返し、definition、node/edge/config、
  runtime snapshot、source node IDを返さない。draft/test executionも公開アプリから参照できない。従来のdefinition／node debug APIは
  `workflows.edit`へ制限し、`workflows.run`だけのoperatorは公開アプリAPIを利用する境界へ修正した。
- 公開時にtrigger inputsと`output.render`からJSON Schemaを生成してimmutable `WorkflowVersion`へ保存。versioned description列を
  SQLite light migrationへ追加し、既存公開版の空contractは起動時に安全なsnapshotからbackfillする。draftの定義・説明変更は再公開まで
  公開アプリへ反映されない。
- editor PreviewとRunnerで13入力型とtyped rendererを共有する`RuntimeComponents`を追加。iPhone下部navigationのWorkflowをRunnerへ置換し、
  editorはedit権限の利用者だけに表示する。AI assistantの公開workflow実行も公開アプリAPIへ移行した。
- エディタの主操作を「更新して開く／アプリを開く」、日常実行面を「公開アプリ」へ整理。差分がある場合だけ保存・公開検証・version更新してdeep linkへ移り、公開済みならversionを増やさず開く。ワークフロー一覧は公開履歴があれば既存公開版を開け、編集中draftを暗黙更新しない。URL queryで選択を再読込後も復元し、存在しない／非公開IDは無限loadingにせず復帰案内を表示する。
- `llm.chat`は管理中のOllamaを既存provider adapter経由で自動ロードし、llama.cppはsystemd user unit起動とhealth完了を待つ。既定有効、startup timeout 240秒（10〜600秒）、同一endpoint/modelの多重起動抑止、進捗表示、外部endpoint素通しを実装した。
- 承認待ちAPIをeditor／公開アプリ共通の型付きcontractへ統一。公開アプリで発生していた承認待ち取得時の500を解消し、redact済み承認文、担当者、ISO 8601期限、承認／却下を320pxでも同じ実行カード内に表示する。
- Project LabとApplication Builderの新規要件を監査し、`docs/design-workflow-runner-project-lab.md`と
  `docs/design-application-builder.md`へ共通IR、決定的generator、Phase A限定初回PR、反復GUI editor、structured AI patch、
  design system、platform advisor、Web/Avalonia/Tauri優先、build/artifact境界を記録した。

検証: backend全323件、frontend production build成功。実ControlDeck serviceを再起動し、healthを確認。
認証付きPlaywrightで320×700から公開workflowを作成・公開し、公開アプリでparagraph入力→Markdown output、過去入力再利用、一覧の公開版button、deep link、reload復元、
キャンバス／内部node名非表示を確認。320×700、390×844、768×1024、1280×800でdocument/body横overflow 0、console error 0。
さらに公開版のhuman approvalで承認文・担当者・期限を確認し、320×700の同じ画面から承認して成功outputへ到達するtargeted E2E 2件を実サービスで確認した。
backend testではrun-only operatorがRunnerを利用でき、definition/list/debug execution APIは403になることを確認した。
実機では未ロードの`qwen3.6-27b-q5_k_m`を`llm.chat`から起動し、provider load→loaded確認→本文生成に成功。確認後は元のunloaded状態へ復元した。

## Workflow Phase 3 human approval／control merge（2026-07-19）

- 隠し共通設定だった承認gateを正式な`human.approval` nodeへ昇格。上流変数を使う承認文、ユーザー名による
  承認者限定、0.1秒〜24時間の期限、承認／却下の監査、承認後のtyped outputをcatalog・metadata・UIへ統合した。
- 承認待ち情報を実行パネルに表示。resolved secretは表示・node output前にredactし、却下は
  `APPROVAL_REJECTED`、期限切れは`APPROVAL_TIMEOUT` Error Contextとしてerror／timeout routeへ渡す。
- `control.merge`を追加し、wait_all、first_success、first_complete、quorum、collectをengineの到着順・成功状態と統合。
  直接上流だけを`items[{node_id,status,output}]`、`values`、`value`へまとめ、成功0件／quorum未達は明示errorにする。
- semantic checkに承認期限、merge方式、入力本数、quorum範囲を追加。node referenceとREADMEを標準45 nodeへ更新した。
- 当時process memory上だった承認待ちは、現在は`WorkflowPause` migration、token hash、schema修正入力、
  service再起動継続まで実装済み（本書冒頭の完了記録を参照）。

検証: backend全295件、frontend production build、実ControlDeck service再起動に成功。390×844 E2Eで
公開版のtrigger→2並列node→wait_all merge→指定ユーザーのhuman approval→typed status outputを実行し、
承認文と承認者表示、Web UIからの再開、merge count=2、横overflow 0、console error 0を確認した。

## Workflow Phase 3 型付きError Context／視覚的error route（2026-07-19）

- node失敗時の共通出力を`error` objectへ統一し、node ID/type、message、code、retryable、attempt、
  redact済みinput summary、timestampを後段へ渡す。node runの`error_json`にも同じ有限snapshotを保存する。
- `on_error=branch`のnodeへ赤い「失敗」と橙の「時間切れ」handleを分離して表示し、edgeも赤破線／橙点線で識別する。
  timeout専用edgeがない既存definitionは従来のerror edgeへ流す後方互換fallbackを維持する。
- inspectorに共通node timeout、retry、失敗動作と直近Error Contextを集約。変数pickerから
  `error.message/code/retryable/attempt/timestamp/input_summary`を挿入できるようにした。
- semantic checkへ無効な`on_error`、0.1秒未満／非数値timeout、未接続error route、重複error routeの検査を追加。
  backend metadataにも共通実行制御schemaを公開し、frontend固有の暗黙設定にしない。

検証: backend全291件、frontend production build、実ControlDeck service再起動に成功。320px起点のPlaywright E2Eで
preview／通常実行／回帰test／公開／履歴／node単体実行／部分再実行／pinned dataに加え、390px inspectorから
node timeoutとerror branchを設定し、失敗／時間切れhandleの表示、横overflow 0、console error 0を確認した。

## Workflow Phase 3 確定的data node（2026-07-19）

- `data.template`を追加。既存の上流変数と任意JSON `data`をMustache/Jinja風`{{...}}`で展開し、textまたは
  構文検証済みJSONを返す。式、関数、attribute access、任意codeは実行せず、入力／出力を2MiBに制限。
- `data.filter`を追加。最大10000件のarrayへnested fieldのtruthy/exists/equality/contains/数値比較、
  unique、stable sort、limitを順に適用し、結果／入力件数を返す。異種値sortも決定的な順序に正規化。
- `data.aggregate`を追加。最大10000件のarrayを任意fieldでgroup化し、count/sum/avg/min/maxを返す。
  count以外はnumberを要求し、文字列の暗黙変換による誤集計を拒否。
- executor、LLM catalog、required config、metadata、output schema、frontend node definition／詳細説明を同時更新し、
  consistency testの集合一致を維持。標準nodeは43種類となった。

検証: backend全289件成功、frontend production build成功。実ControlDeck serviceを再起動し、390×844 E2Eで
trigger入力→filter→aggregate→template→typed outputを実行して`kept=2, sum=30.0`を確認。mobile node libraryで
3 nodeの検索・表示、横overflow 0も確認し、test workflowと一時userは削除済み。

## README機能ガイド拡充（2026-07-19）

- READMEの直近追加を現行実装へ更新し、標準43ノード、実行snapshot、node run、部分再実行、固定データ、
  回帰テスト、draft／公開版、`output.render`、共有Deep Researchエンジンを反映。
- ダッシュボード／監視、アプリ／health check、Web terminal、remote desktop、AI assistant、Deep Research、
  model、Knowledge/RAG、file、GitHub、power、security、PC／iPhone navigationについて、特徴だけでなく
  操作の開始点、使い分け、安全境界、mobile gesture、運用上の注意を機能別ガイドとして追加。
- workflowは入力定義→安全preview→通常test→node単体／部分実行→回帰test→公開の8stepへ整理し、
  typed final output、draft／published／pinned dataの役割と公開preflightをREADMEだけで追えるようにした。
- README内の相対linkの存在とMarkdown差分を確認。実装の詳細・検証証跡は本ファイルと各design documentへ誘導する。

## AIアシスタント standalone PWA下端余白修正（2026-07-19）

- ホーム画面追加から全画面起動するiPhoneでは`env(safe-area-inset-bottom)`が有効になり、AI入力composerの外側へ
  約34pxのpaddingを加えて入力カード全体を持ち上げていた。通常browserのviewport検証ではSafe Areaが0のため再現しない条件差を特定。
- `/assistant`をアプリshellの全画面routeへ追加してモバイル下部navigationの予約領域を除去し、composerの追加下paddingを0へ変更。
  入力カード背景をdialog最下端まで連続させ、空白帯を作らない。
- 追加確認で、standalone PWAでは外側のfixed shellと内側`100dvh` dialogが異なる高さになり、shellの黒背景が
  下端へ露出する条件を確認。dialogをshell基準の`height: 100%`へ統一し、shell／dialog／composerの下端を一致させた。
- token生成／音声状態行を入力カード内の固定24px footerとして入力欄の下側へ統合。待機時も同じ領域へ
  keyboard hintを表示し、状態の出現／消失でcomposer高や入力欄の座標を変えない。footerは入力カードと同じ背景を使い、
  dialog最下端まで連続させるため、独立した黒い空欄を作らない。
- 入力カードを少し囲っていたcomposer外面の背景色と上borderを撤去して透明化。入力カードと固定status footerだけを
  操作surfaceとして残し、周囲に別の薄い帯や箱が見えない構成へ整理した。
- Playwrightを`navigator.standalone=true`で起動し、320×700とiPhone相当390×844のscreenshotを目視確認。
  dark themeの390px条件で`shellBottom = dialogBottom = composerBottom = inputCardBottom = 844px`、composer padding 0px、
  document幅390pxを実測。音声状態の表示前後も入力欄top座標が不変で、状態footerが入力欄の下にあることを確認。
  モバイル下部navigation非表示、frontend production build、実ControlDeck service再起動も成功。

## モバイル横overflow・ターミナル右端タッチ修正（2026-07-19）

- iPhone Safariで16px未満のinput/select/textareaへfocusするとVisual Viewportが自動拡大し、keyboard表示後に
  右へpanした状態が残ることを横はみ出しの主因として特定。767px以下ではフォームの実効font-sizeを16px以上へ統一した
- `w-screen`（100vw）がscrollbar幅やVisual Viewportとの差分を含んでsheet/drawerをdocument幅より広げるため、
  BottomSheet、Drawer、workflow SampleBookを`width: 100%`かつ`100dvw`上限へ変更。html/body/rootもdocument幅でclipする
- terminal rootのVisual Viewport追従幅をlayout viewport以下へclamp。xtermの右scrollbarはcoarse pointerのモバイルで
  1px予約へ縮小し、文字領域を削らない20px幅のoverlay履歴barへ置換。barのtapは対応位置へjumpし、dragは
  指位置へ連続追従するがIME textareaへfocusさせない。端以外のtap入力とterminal面全体の上下swipeも維持する
- 320x700 / 390x844でreload後と文字入力focus後のdocument横overflow、実効font-sizeを確認するE2Eと、
  terminal右端touchではkeyboard入力へfocusせず中央touchではfocusするE2Eを追加した

検証: backend全278件成功、frontend本番build成功。実サービスを`./deck.sh`で再起動し、Playwright Chromiumの
terminal回帰18件成功・任意10分soak 1件skip。320px/390pxとも横overflow 0、overlay barのtap/drag、
IME、100/300KB・UTF-8 paste、keyboard 10回開閉、再接続、履歴、desktop wheelを確認。テスト用ユーザーは削除済み。

## ワークフローキャンバスのiPhone操作統一（2026-07-19）

- node inspectorを88dvhの固定surfaceへ変更し、node種別や設定/input/output/error tabの内容量が変わっても
  sheetのtop/heightを維持。削除を設定末尾からheaderの44px actionへ移し、どのtabからも同じ位置で操作できる
- node handleは12pxの見た目を保ったままmobileの透明hit areaを周囲16pxへ拡大し、node外へ出た領域をclipしない
- edgeの透明選択幅をmobileで32pxへ拡大。選択時にaccent強調と固定toolbarを表示し、44px削除action、
  source/target端点の36px reconnect radiusによる付け替えを追加。変更は既存definition形式のままdirty管理する
- 操作契約を`docs/design-workflow-integrated-ide.md`へ記録し、常時buttonをnodeへ載せず選択時だけ段階表示する

検証: frontend本番build成功、実サービス再起動成功。320px E2Eでedge選択、source/target reconnect端点、edge削除、
handle hit area、inspectorのtab切替前後のtop/height一致、headerからのnode削除、横overflow 0、console error 0を確認。

## Model画面のOllamaロード状態追従修正（2026-07-17）

- チャット等によるOllamaの暗黙ロード後も、15秒間隔のモデル一覧cacheにより左インジケータと右操作ボタンが
  未ロード表示のまま残る問題を修正。軽量な`/models/running`（Ollama `/api/ps`）を表示中のみ2秒間隔で取得し、
  インジケータ、VRAM表示、「ロード/アンロード」ボタンを同一のlive stateから描画する
- 画面上のロード/アンロード操作完了時はlive stateを即時更新し、再取得完了を待つ間の連打を防ぐ処理中表示を追加
- backendも`/api/tags`と`/api/ps`間の`name`/`model`、`:latest`省略、大小文字、digestの表記差を正規化し、
  Ollama更新やローカル登録モデルでもロード判定が欠落しないようにした

検証: backend全267件成功、frontend本番build成功。実機Qwen3.6 27Bを32K CTXでロードしてbackend判定を確認し、
320px幅Playwrightで外部ロード後2秒以内に「未ロード/ロード」から「ロード中/アンロード」へ変わることを確認した。
検証後はQwenをアンロード済み。

## モデル個別出力tokenへの統一（2026-07-17）

- ⚙共通設定の「チャット・ワークフロー生成の出力token上限」を撤去。通常チャットとワークフローJSON生成は、
  Ollamaのモデル個別`num_predict`、llama.cpp instance個別`n_predict`を同じresolverから使用する
- `-1/-2`等の無制限指定はplatform安全上限262,144 tokenへ正規化。モデル個別値を持たない外部OpenAI互換
  endpointだけ8,192 tokenへフォールバックし、管理中モデルの設定を共通値で上書きしない
- モデル個別の通常/Deep Research CTX、Ollama `num_predict`、llama.cpp `n_predict`、Deep Research総出力に
  262,144 token presetを追加。Deep Research policyの保存上限も256Kへ拡張した

検証: backend全266件成功、frontend本番build成功。Ollama/llama.cpp個別値、無制限値の256K正規化、
外部endpointの8K fallbackを自動テストし、共通設定から重複項目が消え個別設定に256K presetがあることをUI確認した。

## Deep Research共有エンジン・ノード・ローカル資料統合（2026-07-19）

- `research.deep`に残っていた短い検索結果の単発要約器を廃止し、AIアシスタントの反復型Deep Researchエンジンへ統合。
  計画、最低2回の探索、coverage再評価、SearXNG、公開本文/PDF、学術横断、GitHub構造解析、特許/市場、6章継続生成、
  引用検証、Deep Research専用CTX、進捗をノードとアシスタントで共有する
- quick（2 round/8検索）、standard（3/16）、deep（4/24）、exhaustive（最大6/36）とcustom budgetをノード設定へ追加。
  source portfolio、SearXNG category、RAG collection、ローカルproject、根拠context、report token上限を設定可能にした
- AIアシスタント設定にも検索深度とWeb/PDF・学術・GitHub・直接URL・添付/RAG・ローカルコード・特許・市場の選択を追加。
  添付PDF/文書は会話RAGからDeep Researchへ再利用する
- ローカルコードadapterを追加。`files.allowed_roots`を通したrealpath検証、symlink/秘密ファイル/依存物除外、最大5,000 entry・
  最大12主要ファイルの有限読取、Python/TypeScript静的symbol索引により、コードを実行せず構造・テスト・CIを根拠化する
- 旧`arxiv/crossref/local` source設定は`academic/local_code`へ実行時aliasし、既存workflowを壊さない。出力は旧`findings/count`を
  維持しつつ、共有契約の`sources/research/sub_questions`を追加した

検証: backend全282件成功、frontend本番build成功。実サービスを再起動し、AssistantのDeep設定を含む
320×700/1280×800 E2Eで横overflowなしを確認。実機llama.cpp Qwen3.6-27Bで`research.deep`ノードから
ControlDeck自身を`local_code`限定・quick budgetで評価し、295.9秒、2 round/4 search、4,044文字、引用31件、
不正引用0、引用段落率100%で完走した。この評価で空URLのローカル/RAG資料が`/`へ正規化され1件に誤dedupeされる
問題を発見し、空URLはtitle/pathをkeyにする修正と回帰テストを追加。主要12ファイル/14根拠候補、先頭5件のunique key、
秘密・symlink・依存/cache除外も決定論的に再確認した。
ローカルSearXNGもオンデマンド起動し、`general,it`カテゴリ指定で3件のJSON検索結果を実取得した。

## ワークフロー実行スナップショット・当時版再実行基盤（2026-07-19）

- `WorkflowVersion`へ連番、input/output schema、checksum、published_atを追加し、同一定義checksumは実行間で再利用。
  `WorkflowExecution`へversion ID、redact済みdefinition snapshot、runtime snapshotを追加した
- runtime snapshotにはnode version、LLM endpoint/model/sampling、Python version、利用可能なsecret名だけを保存。
  定義へ直書きされたpassword/token/API keyは`***`にし、`{{secrets.NAME}}`は値を持たない参照名として残す
- `WorkflowNodeRun`を追加し、node ID/type/version、status、redact済み上流入力、出力、error、token usage、開始/終了、
  elapsed、attempt/retry、cache source、schema versionをノードごとに独立保存。巨大化防止の有限JSON上限も設けた
- `GET /workflows/{id}/versions/{version_id}`、`GET /workflows/{id}/executions/{execution_id}/nodes`、
  `POST /workflows/{id}/executions/{execution_id}/retry`を追加。retryは`current/historical`を明示選択し、入力を再利用する
- 実行履歴sheetへ「現在のフローで再実行」「当時のフローで再実行」を追加し、node runの時間・retry・実出力を表示。
  Workflow削除時はnode run → execution → versionの順に削除してFK整合を維持する
- SQLite軽量migrationへ既存version/executionの追加columnを登録。`workflow_node_runs`は`create_all`で冪等作成する

検証: backend全283件成功。current/historicalで異なる出力になるAPI回帰、秘密値非保存、node run、version detail、
削除順序を確認。frontend本番build成功。実サービス再起動で既存SQLiteへ追加5 version column、3 execution column、
`workflow_node_runs`テーブルが作成されたことをinspection。Playwrightでpreview/test/過去入力に加え、履歴sheetのnode run、
現在版/当時版再実行ボタン、320×700で横overflowなしを確認した。

## ワークフローノード単体実行・固定データ・部分再実行（2026-07-19）

- `WorkflowPinnedData`をdraft補助データとして追加し、workflow/nodeごとにredact済み出力と元execution IDを保存。
  定義・`WorkflowVersion`・published版には含めず、本番実行は固定データを参照しない。1MB上限、pin/unpin監査、workflow削除時の
  FK順序を実装した
- inspectorの実行tabから、最新成功実行、指定した直近実行、手動JSON、固定データを選び、保存済み上流contextで単一executorを
  実行可能にした。固定データ選択時はexecutorを呼ばず`CACHED`を返し、キャンバスにも`📌 固定`を表示する
- `POST /workflows/{id}/nodes/{node_id}/run-to`を追加。対象ノードの祖先だけをDAGから抽出して実行し、下流の外部送信・書込みを
  起動しない。`POST .../resume-from/{node_id}`は過去contextの祖先出力を保持し、現在版/当時版を選んで対象以降だけを再計算する
- 部分実行前に未保存draftを保存し、runtime snapshotへ`run_to_node_id` / `resume_from_node_id`を記録。
  output variableも保存済み祖先contextから再構築する

検証: backend全284件成功。API回帰で単体実行、秘密keyの固定時redact、executor不使用cache、対象まで実行時の下流除外、
現在版での途中再開と旧上流値の再利用、node run列を確認。frontend本番buildと実サービス再起動に成功し、SQLiteの
`workflow_pinned_data`作成もinspection。320px Playwrightで単体実行、pin表示/解除、対象まで実行、途中再実行導線、
横overflowなし、console errorなしを確認した。検証用workflow/user/pinは削除済み。

## ワークフロー回帰テスト（2026-07-19）

- `WorkflowTestCase`へ名前、redact済み入力、mock境界、期待出力、追加assertion、直近execution/resultを保存。
  literal secretは入力・期待値・assertion間のコピーも含めて永続化前に除去し、各JSONを1MB以内へ制限する
- test case CRUD、単独run、全case batch run APIを追加。実行ごとに現在のdraftをversion snapshot化し、期待outputの完全一致と
  `exists/not_exists/equals/contains/gt/gte/lt/lte` assertionを決定論的に評価する。結果にはpath、期待値、実値、個別合否を残す
- Preview Workspaceの入力・通常テスト結果と同じ画面へ回帰テストを統合。現在入力、または成功時の最終出力からcaseを作成し、
  入力再読込、単独/一括実行、成功/失敗、assertion件数、失敗差分、削除をモバイルでも操作できる
- workflow削除時はtest caseをexecutionより先に削除し、`last_execution_id`のFK整合を維持する

検証: backend全285件成功。API回帰で2 case一括実行、成功/失敗差分、3 assertion成功、秘密入力の`***`化、
case/workflow削除を確認。frontend本番buildと実サービス再起動に成功し、`workflow_test_cases`テーブル作成をinspection。
320px Playwrightで通常テスト結果からcase作成→一括実行→成功判定→入力再読込、横overflowなし、console errorなしを確認した。
検証用workflow/case/userは削除済み。

## ワークフローdraft／公開版分離（2026-07-19）

- workflow本体の`definition_json`を自動保存draft、`WorkflowVersion.published_at`が付いたimmutable snapshotを公開版として分離。
  checksum比較から`編集中`／`公開 vN`を返し、保存後に公開版との差が生じても公開snapshotを変更しない
- `POST /workflows/{id}/publish`を追加。構造・意味検証、最終output有無と名前重複、secret存在、pinned data残存、
  回帰テスト状態、quality scoreをpreflightし、blocking issueが1件でもあれば409で公開しない。公開操作は監査する
- 通常の「実行」、schedule、Webhook、system event、`flow.call`は公開版だけを選択し、未公開workflowは明示エラーにする。
  Preview Workspaceの通常テスト、test case、node run-to/resumeはdraft開発経路として分離を維持する
- 後方互換migrationとして、導入時点ですでに`enabled`だった自動実行workflowだけは起動時に現在定義をlegacy baseline公開版へ
  1回移行する。新規workflowは公開前にenableできず、再起動を利用した検証回避はできない
- desktop command barに状態badgeと公開button、mobileの三点menuに公開actionを追加。未保存変更は先に保存し、保存失敗時は公開を中断する

検証: backend全286件成功。未公開本番実行の拒否、公開後の本番出力、draft変更後も旧公開版を実行すること、draft testは新値を使うこと、
pin残存時の公開拒否、解除後の再公開、Webhook/subflow/approvalの公開版回帰を確認。frontend本番buildと実サービス再起動に成功。
320px Playwrightで回帰case合格後のmobile公開、公開toast、横overflowなし、console errorなしを確認した。
検証用workflow/version/case/userは削除済み。

## 型付き最終出力 output.render（2026-07-19）

- `output.render` executor/metadata/catalog/validation/frontend定義を追加。Auto、text、Markdown、JSON tree/raw、Table、
  Key-value、Code、Image/Gallery、Audio、Video、File、Link、Status、Metric、Progress、Citation listを選択できる
- name/title/description/value/renderer/schema、download/copy/collapse、sensitive、filename/MIMEを設定し、全実行経路で
  `name/type/value/source_node_id`と表示metadataを同じ最終output contractとして返す。JSON系rendererは文字列を型付き値へ復元する
- Preview Workspaceは画像、リンク、表、音声、動画、JSON/code、その他を型別表示。`sensitive`出力値はlive後段では利用可能だが、
  DB・履歴・API保存時に`***`化する。旧`signal.display`は後方互換aliasとして維持し、新規作成ではtyped outputを推奨する
- READMEへ入力→preview→単体/部分実行→回帰→公開の操作手順、typed output、draft/公開/pinの違いと安全境界を追記した

検証: backend全287件成功。typed table contract、title、JSON配列復元、sensitive保存redact、公開preflightを回帰で確認。
frontend本番buildと実サービス再起動、health確認に成功。320pxの共通Preview Workspaceは既存E2Eで横overflowなしを維持し、
型別rendererのブラウザ個別操作は次のE2E拡充対象とする。

## AIアシスタント Deep Research超強化（2026-07-17）

- 数件の資料提示で停止していた原因を、固定3クエリ・本文8件・単発要約・最終生成HTTP timeout 300秒と特定。
  `docs/design-deep-research-engine.md`へ調査状態機械、source portfolio、有限資源、引用品質、CTX切替を詳細設計した
- LLMによる調査計画から、最低2/最大4ラウンドでcoverage、未解決点、矛盾を評価し、検索語をpivotするagentic loopへ変更。
  最大24検索、120候補、本文32件、最終根拠36件、根拠context 90,000文字、レポート8,192 tokenとし、
  進捗と品質指標をserver job/message metaへcheckpointする
- Webに加えてOpenAlex/Crossref/arXiv/Europe PMC/DBLP/DOAJ、PatentsView特許、SEC EDGAR、直接URL、
  HTML/text/PDFをsource portfolio化。失敗sourceは調査全体を落とさずcoverage limitへ明示する。
  PatentsView keyは暗号化Workflow Secret `PATENTSVIEW_API_KEY`を再利用し、ログへ出さない
- GitHub URLを検出するとrepository metadata、recursive tree、README/manifest、主要source、test、CIを取得。
  Python ASTとTypeScript/JavaScriptの保守的静的抽出で関数、クラス、変数、import/export、API route、
  観測可能な呼び出しを索引化し、構造・データフロー・既存機能の統合可能性をpath付きで評価する
- 引用番号の実在、引用資料数、根拠付き段落率、本文長を決定論的に評価し、coverage 55%未満等は根拠を増やさず
  1回だけ引用修正する。最終資料は会話内文献ID `R1…`へ変換し、後続会話で必要分だけ再展開する
- 一律256Kへ変える共通CTX設定を撤去し、Ollama/llama.cppの各モデル個別設定へDeep Research専用CTXを追加。
  未指定なら同じモデルの通常CTXを使用して何も変更しない。異なる場合、Ollamaはrequest単位で適用後に通常optionsへ、
  llama.cppは開始前に専用CTXで再ロードし、成功・失敗・キャンセル後に通常CTXと元の稼働状態へ必ず復元する
- AI画面の詳細へround、検索回数、候補/採用資料、GitHub解析数、coverage、引用段落率、CTX適用を表示する
- 最終レポートが単発8,192 token上限で途中終了しても検出していなかった不具合を修正。6章を独立生成し、
  完結markerが無い章は続きから最大8回生成して重複除去・結合する。総出力は既定32K/最大128K token、
  各章へ均等配分し、完結章数と未完結候補をUIへ表示する。短い改稿で長い草稿を置換しない長さ検証も追加

検証: backend全264件成功、frontend本番build成功。Model設定E2Eと、認証付きAssistant E2Eの320x700 / 1280x800で
256K CTX表示、探索指標、文献ID、横overflowなしを確認。実機Ollama Qwen3.6-27Bで`num_ctx=262144`、
Web・専門検索・GitHub構造取得を4ラウンド/検索24回実行し、81件の証拠候補から23件を最終選定。
20分6秒で5,860文字、引用101箇所/12資料、不正引用0、引用段落率100%のレポート生成を完了した。
従来の300秒timeoutを実機再現して1,800秒へ修正した。公開GitHub branchがローカル最新実装より古く、モデル評価が
現行実装と食い違うsource freshness限界も検出したため、公開時点・取得限界をcoverageへ残す運用とした。
途切れ不具合は実機Qwen3.6-27Bへ128 tokenで長文を要求し、`done_reason=length`、完結markerなし、253文字で終了する形で再現。
章の初回出力が同様に途切れるfixtureで全6章が継続・完結する回帰テストを追加した。

## Model設定分離・ファン表示・プラットフォーム再読み込み（2026-07-17）

- Model画面の⚙を全runtime共通設定だけに限定し、共通CTX項目とprovider/モデル個別設定を撤去。
  Ollama/llama.cppのモデル行から開く画面には、そのモデル固有の生成・ハードウェア・通常/Deep Research CTXだけを表示する
- GPUはAMD sysfsの`fan1_input`とamd-smiのRPMを取得し、ホームのGPU使用率カードへ温度と併記。
  CPUはpsutil hwmonでCPUと明示されたfanだけを採用し、筐体/PSU/GPUの誤表示を避け、取得不能時は`N/A`とする
- 操作シートの電源付近へ「Control Deckを再読み込み」を追加。固定引数のsystemd user transient unitで
  Webサービスを応答後に再起動し、ブラウザはhealth復帰を監視して自動reloadする。実行は認可し監査ログへ記録する

検証: backend全264件成功、frontend本番build成功。認証付きPlaywrightでModel個別/共通分離とDashboard fan表示を確認し、
320x700 / 1280x800とも横overflowなし。実機GPU fan 889 RPM、CPU fanセンサー非公開のためN/Aを確認。
platform reload APIは202応答後にservice PID `1607245→1609074`、health復帰を確認した。

## AIアシスタント 会話内文献レジストリ（2026-07-17）

- 詳細設計を`docs/design-ai-chat-reference-registry.md`へ記録。Webページ、論文、資料等を会話単位の
  `chat_references`へ永続化し、`R1…R9, RA…RZ, R10…`の短い36進IDを割り当てる
- URL正規化+SHA-256キー（URLなしはタイトル+provider）で会話内重複を排除。同じ出典を複数回の調査で
  取得してもIDを維持し、会話削除時は文献も削除する。同時登録はDB unique制約を正本に最大3回再評価する
- Web・学術・Deep・複合調査のLLM根拠、回答引用、message meta、WebSocket sourcesを`[R英数字]`へ統一。
  Deep Search内部の一時連番も永続IDへ変換してから保存する
- 後続入力の`R1` / `@RA` / `[RA]`を検出し、同じ会話に存在する指定文献だけをLLMへ展開する。
  保存抜粋6,000文字/件、最大12件、合計18,000文字で制限し、全出典本文の常時注入によるCTX圧迫を避ける。
  存在しないIDは推測で補わないsystem指示を追加した
- provider非依存の文献ツール境界として、軽量一覧、1件取得、最大12件の一括解決APIを追加。
  Ollama、llama.cpp Vulkan/ROCm、その他OpenAI互換runtimeで共通利用し、将来のfunction callingも同じserviceへ接続できる
- 出典カードを「会話内文献」へ変更し、短いIDバッジと36pxの「参照」操作を追加。押すと入力欄へ
  `[R1] `を挿入し、そのまま後続質問を書ける

検証: backend全254件成功、frontend本番build成功。実サービス再起動後に`chat_references`作成とhealthを確認。
認証付きPlaywright Chromiumの320x700 / 1280x800で文献ID・参照操作を表示し、入力への`[R1]`挿入、
document横overflowなしを確認。採番境界`R9→RA` / `RZ→R10`、URL重複、一覧/個別/一括解決、
選択文献だけのコンテキスト注入、会話削除を自動テストした。

## AIチャット UI・自動モード・長文ストリーム・音声入力（2026-07-17）

- 詳細設計を`docs/design-ai-chat-auto-mode-asr.md`へ記録。利用者の追補指定に従い、長時間処理を含む
  実行前確認は挟まず、自動判定後に開始する。モードは通常「自動」で、入力からchat/Web/学術/Deep/
  ワークフロー生成・実行を判定し、理由を表示する。必要な場合は単一メニューで明示上書きできる
- AI画面を他タブと同じzinc/accent、中央コンテンツ幅、段階開示、Safe Areaへ統一。右上の閉じる操作は
  44pxタッチ領域、枠・影・強いコントラスト、PCの「閉じる」ラベル、focus ringを持つデザインへ変更
- 常設の6モードpill列を廃止し、自動判定status + mode menuへ集約。ワークフロー生成意図は確認なしで
  server jobによる生成→検証→登録→動作確認→最大4回の自動修正へ進む
- 自動判定を決定論ルール + LLMプランナーの二段構成へ拡張。明確な依頼は即時判定し、曖昧・複合的な依頼は
  temperature 0/thinking off/JSON Schemaで`chat/Web/学術/複合調査`と検索手順を生成する。不正JSONやprovider失敗時は
  通常対話へフォールバックする。Ollamaはnative `format`へJSON Schemaを渡し、thinking modelが推論だけで出力上限を
  使い切ってJSONを途中切断する問題も修正
- 構造化出力dialectをruntime provider共通層へ集約。OpenAI標準JSON Schema → JSON Object → prompt制約のみの
  段階fallbackをOllama、llama.cpp Vulkan/ROCm、その他OpenAI互換で共有し、LLMノードとGraphRAG抽出にも適用。
  Ollama native `format`はprovider固有の最適化として残し、契約自体は依存させない
- 複合調査はWeb・学術検索を併用し、URLで出典を重複排除。LLMが情報不足を再評価して標準3回/上限5回、
  検索呼び出し合計8回まで追加調査し、引用付きで要約する。判定計画・検索・評価・要約の進捗は永続jobと
  chat message metaへ保存し、画面再接続後も復元・表示する
- ヘッダー左上へ現在機能を常時表示。自動時は`自動判定: Web検索`、明示選択時は`選択: 学術検索`の形式とし、
  右側のmode menuと役割を分離した。320pxでは会話切替をヘッダー2段目へ配置して44px操作領域を維持する
- 機能選択menuを会話履歴の左へ移し、機能選択・狭幅履歴・履歴削除を同じ行へ統合。左上概略と重複していた
  判定理由のContext barは行全体を削除し、会話本文の表示領域を広げた。幅は従来の機能選択112px・
  履歴可変幅（320px時132px）を維持し、高さだけ両方36pxへ抑えて同一角丸・shadow/focus表現へ統一
- 会話切替の右端へ44pxのゴミ箱ボタンを追加。選択中の履歴を確認なしで即時削除し、新しい空の会話へ切り替える。
  設定内の削除操作も同じ確認なしの挙動へ統一
- 削除後に空会話を即DB作成して「新しい会話」が履歴へ残る不具合を修正。初期表示・新規・削除後は未保存下書きとし、
  最初の送信時だけ会話をDB登録する
- 長文出力が約300 deltaで止まる原因を、bounded `Job.events`の配列長をcursorに使っていた不整合と特定。
  単調増加event sequence/offsetへ変更し、購読遅延時と完了時はDB全文snapshotへ収束する。
  frontendも40ms単位のdelta反映、最大5回の指数backoff再接続、利用者が末尾付近にいる場合だけの追従へ変更
- 入力欄左に44pxのマイク/停止ボタンを追加。1.2秒無音または30秒上限で確定し、ローカル認識結果を
  直接送信する。LLM回答中はミュートし、停止/unmount/失敗時にMediaStream、AudioContext、timerを解放する
- 初回マイク操作でwhisper.cpp v1.9.1と日本語精度を優先した多言語`large-v3-turbo`モデルをbackground job導入する。
  保存先はGit管理外の`~/.local/share/control-deck/runtimes/whisper.cpp/v1.9.1`。モデルは1,624,555,275 bytesと固定SHA-256を検証し、
  静的linkしたruntimeのinstall revisionも一致する場合だけ再利用する。音声は25MiB上限で一時領域へ保存し、
  ffmpegで16kHz mono PCM化、認識後は成功・失敗とも削除する
- 通常回答とワークフロー生成の出力上限は当時の共通既定を8,192 tokenへ変更（後にモデル個別設定へ統一）

検証: backend全251件成功、frontend本番build成功。実機でwhisper.cppをsource buildし、`large-v3-turbo`モデル取得・hash検証に成功。
2回目は0.33秒で既存runtime/modelを再利用した。Wikimedia Commonsの公開日本語音声`Ja-happyou.ogg`を
同じ変換・認識関数へ通し、6.92秒で`発表`と認識。実サービス再起動とhealthを確認。認証付きPlaywright Chromiumで
320x700/1280x800の横overflow 0、textarea 16px、マイク/閉じる/履歴削除44px、自動Web/フロー生成判定、
履歴の確認なし削除→新規会話切替、無音MediaStreamで録音開始→停止→idle復帰、console errorなしを確認した。
実機Qwen3.6-27B + Ollamaでは曖昧な依頼を12.67秒で`research`、Web/学術4手順、最大4反復として有効JSON判定。
Web+学術各1手順の実ジョブも46.22秒で完了し、出典18件、本文1,421文字、計画・進捗4件をDBへ保存した。

## モバイルターミナル閉じるボタン（2026-07-17）

- 全画面ターミナルの閉じる操作をヘッダー右端へ固定し、44px以上のタッチ領域、明確なborder/background/shadow、
  accent focus ringへ統一。PCでは「閉じる」ラベル、320pxでは視認性の高いXアイコンを表示する

## Claude修復コンソールの撤去（2026-07-17）

- Web起動のたびに`seed_repair_app()`が「Claude 修復コンソール」を再登録していた処理を廃止
- 専用`scripts/claude-repair.sh`を削除。既存環境では旧seed由来と判定できる登録だけを起動時に削除し、
  `cdapp-*` systemd user unitの停止・撤去と監査ログ記録を行う
- 同名でも専用scriptを参照しないユーザー登録アプリは削除しない

検証: backend 236件成功。実サービス再起動で旧app ID 5、`cdapp-5.service`、専用scriptを撤去し、
`app.retired_remove`監査ログ1件を確認。2回目の再起動後も登録0件・監査ログ1件のままで再作成されないことを確認。

## サマリー

| Phase | 状態 |
|---|---|
| 文書整備 | ✅ 完了 |
| Phase 1 — 認証 + レイアウト | ✅ 完了 |
| Phase 2 — アプリ管理 | ✅ コア完了（アイコン・TCP/HTTP/ファイル/許可コマンドHC対応済み） |
| Phase 3 — 監視 | ✅ コア完了（アラート通知、アプリ別CPU/RAM/GPU/VRAMを含む） |
| Phase 4 — ファイル + ターミナル | ✅ コア完了（ごみ箱・再開可能upload・ZIP/tar.gz・PDF/audio/video preview対応済み） |
| Phase 5 — ワークフロー | ✅ コア完了（下記参照） |
| Phase 6 — リモートデスクトップ | ✅ コア完了（guacd トンネル + 接続管理 + ビューア） |
| Phase 7 — TOTP ほか | ✅ コア完了（TOTP/PWA/バックアップ。WoL はワークフローノードで対応） |
| Phase 5b — ワークフロー統合開発環境 | 🚧 Phase 1完了、Phase 2（snapshot/retry/node run/pin/部分再実行/test/event/SSE/Alembic/durable pause/artifact）完了、Phase 3 core（typed output/error、approval/merge/data、明示flow control、durable Delay、Try、System Trigger）完了 |

### ワークフロー統合開発環境 監査・詳細仕様（2026-07-19）

- 実コード、API、UI 導線、自動テストを照合し、React Flow、safe dry-run、metadata、実行履歴、
  WorkflowVersion、approval/error handle、parallel loop を再利用できる基盤として確認
- editor 内 chat、実行入力、dry-run、live/history、node 設定が別 surface に分断され、node run、execution snapshot、
  typed output contract、draft/published、retry/resume、sequence 付き event が不足していることを確認
- 現状・実装場所・問題・再利用判断の監査表、target UI、definition v2、data model、API、execution semantics、
  security、quality、migration、test、Phase/PR 計画を `docs/design-workflow-integrated-ide.md` に記録
- mock 回帰と実ローカル LLM/runtime 評価の二層検証、および全 Phase 後に最低 15 sample と全 node の
  詳細説明を提供する Phase 6 を追加

検証: 文書変更のみ。コード実装・service 動作確認は各 Phase PR で実施する。

### ワークフロー統合開発環境 Phase 1 UX 基盤（2026-07-19）

- editor 内の「チャット」を廃止し、trigger input、実行mode、想定最終output、side effect、safe preview結果、
  通常test結果、node別結果、過去実行input loadを同じ `PreviewWorkspace` に統合
- `POST /workflows/preview-definition`、`POST /workflows/{id}/test`、
  `POST /workflows/{id}/executions/{execution_id}/load-inputs` を追加し、legacy `signal.display` から共通output形式を返す
- trigger inputを boolean/multi-select/date/datetime/file-list/JSON/key-value/secret-reference と説明、初期値、
  placeholder、最大長へ拡張。node inspectorを設定/入力/出力/実行/error/詳細の6 tabへ統一
- 実行情報panelをcanvas下部のdebug panelへ移し、live node status、history、versionを維持
- workflow contextをDB保存/API応答する前に再帰redactし、sensitive keyの値が別outputへコピーされた場合も置換。
  live executor contextは変更せず、secret値をresponse/log/DBへ出さない境界を強化
- Phase 2対象のcache/pinを使うnode単体実行、途中再開、historical/current retryはinspector内に導線を先行表示し、
  誤実行を避けるため未実装buttonはdisabledで明示

検証: backend全278件成功、frontend本番build成功。実serviceを再起動しhealth正常。
Playwright Chromiumで入力 → safe preview → test → final output → 過去input load、inspector 6 tabを確認し、
320×700 / 390×844 / 768×1024 / 1280×800で横overflow 0、console/page error 0。一時user/workflowは検証後0件。

## README の現行機能反映（2026-07-17）

- 2026-07-13以降に追加された独立AIアシスタント、LLM provider / llama.cpp複数GGUF管理、Knowledge/RAG、
  ワークフロー安全プレビュー、ジョブ基盤、ファイル・アプリ管理強化、モバイル改善を主機能と直近追加へ反映
- OpenCodeについて、通常起動では導入・有効化しないオプトイン境界、管理prefixへの導入、PATH上の既存導入、
  有効化後のendpoint/model/project/operation設定、disable/uninstallの違いをREADMEへ追加
- `deck.sh` の現行サブコマンドとOpenCode実装・詳細設計を照合し、READMEから設計文書への導線を追加
- ワークフローは標準39ノードと条件登録の`code.agent`を区別し、生成時の意味検証・品質スコア、catalog、
  安全preview、並列map、scrape viewer、RAG/Deep Researchなど2026-07-13以降の追加内容をREADMEへ反映

### AIワークフロー生成の空JSON応答・出力上限修正（2026-07-17）

- Qwen3.6-27B + OllamaでAIアシスタントの最小ワークフロー生成を再現。従来の簡略`response_format`では
  HTTP 200でも本文0文字となり、「有効なJSONを返しませんでした」になることを確認
- OpenAI互換の標準`json_schema` payloadを初回から送り、非対応providerだけschemaなしへfallbackするよう修正。
  JSON抽出もgreedyな正規表現から完全なobjectを順にdecodeする方式へ変更
- ワークフロー生成の固定800 tokenを廃止し、当時のModel共通出力上限を使用（後にモデル個別設定へ統一）。
  UIへ8K〜131K出力と256K CTX presetを追加。CTXと最大出力は独立設定のまま維持

検証: 修正前の同一最小フローはHTTP 200・本文0文字で再現。標準schema化後は実機Qwen3.6-27B + Ollamaで
本文711文字、3ノード・2エッジを生成し、JSON抽出・構造/意味検証とも問題0。backend 235件、frontend本番build、
再起動後のhealth APIを確認。Playwright Chromiumの320px/1280pxで131072 token presetの表示、横overflow 0を確認。
診断でロードしたOllamaモデルは検証後にアンロードした。

追加検証: 静音profile 210Wで20ノード一括生成を試し、厳密JSON SchemaとJSON objectの両方式が300秒でtimeout。
LLMに18段の処理設計を短いJSONで生成させ、サーバー側でtrigger/resultを含む正規定義へ合成する分割方式では約22秒で成功した。
20ノード・19エッジ、構造/意味error 0、品質78の「LLM 20ノード・テキスト処理デモ 0717-0753」をworkflow ID 2へ登録し、未実行のまま内容確認用に保持。

## Web通信・監視処理の軽量化（2026-07-15）

- 高周波の外向きpingはなく、常時通信は認証済みmetrics WebSocketの2秒更新だった。変更前の実ブラウザでは
  12秒に6 frame、`GET /apps`は5秒周期で3回。`/apps`はsystemd状態・プロセスツリー・待受ポートを走査し、
  平均28.7ms（最大39.1ms）だった
- 主負荷は2秒ごとに起動する`amd-smi metric --json`。実機で1回40〜60ms CPU、最大RSS約25MB、
  約23KB JSONを生成していた。複数AMD GPUからVRAM総量最大のdGPUを選び、同じ主要値
  （使用率・VRAM・温度・hotspot・電力・power cap）をamdgpu sysfsから直読するfast pathへ変更。
  sysfsが不完全な環境だけCLIへfallbackする
- アプリ状態の共有queryを15秒周期へ変更。操作時の楽観更新と完了後invalidate、非表示タブ停止は維持

検証: backend 181件成功、frontend本番ビルド成功。実サービスは`sysfs-amdgpu`で32GB dGPUを選択し、
10秒のservice cgroup CPUは1.67%相当、`amd-smi`周期プロセス0。旧CLI実測分を加えた変更前推計4.2%から
約60%削減。1280pxで31秒確認しmetrics 16 frame（初回含む）、`/apps` 3回、console error・横スクロールなし。
320pxのシステム画面も横スクロール・console errorなし、GPU値とmetrics WS継続を確認。

### 汎用ジョブ制御・Model進捗通信

- 互換用`jobs`表へ`job_controls`表を追加し、owner、冪等キー、priority、heartbeat、revisionを永続化。
  最大4同時実行の安定priority queue、queued/running cancel、再起動時interrupted化を実装
- REST、cancel、全体`WS /jobs/stream`でowner本人とownerなしsystem jobだけを返す。cancelは監査対象
- 個別ジョブstreamの0.4秒pollとModel画面の1〜2秒pollを通知Eventへ置換。全体WS更新を100msで束ね、
  高頻度token/eventでも中間通知を増幅させず最新revisionと最終状態を保持
- Playwright Chromiumの1280px/320pxで12秒撮影・通信計測し、jobs RESTは初回1回、jobs WSは1接続、
  横overflow 0、console error 0を確認。backend 198件、本番build成功

### ターミナルの緑色入力欄・画面欠落の追加再現

- Playwrightの320px touch viewportをキーボード相当の高さ390pxへ縮小して撮影。緑色部分は入力textareaではなく、
  永続化用tmuxの既定status barが最下段で入力欄のように見えていたものと特定
- Control Deckは上部にセッション切替UIを持つため、Control Deckのtmux sessionだけstatus barを非表示化。
  既存の永続sessionにも次回接続時に適用し、表示を1行増やす。他のユーザーtmux sessionへは影響しない

## チャット生成遅延・runtime選択基盤（2026-07-15）

- 実機Qwen3.6-27B + llama.cppでワークフロー生成を再現し、従来は内部推論がctx 2048まで1161 token続いて
  47秒後に本文JSONなしで422となることを確認。ワークフロー生成をthinking off、最大800 token、JSON Schemaへ変更し、
  11.55秒で有効JSON（quality 78）を返すようにした
- 永続チャットを既定thinking offかつ有限出力に変更し、OpenAI互換の`reasoning_content`を本文と分離。
  短文「1+1」の実機応答は初回出力・完了とも0.66秒、本文`2`、thinking 0文字を確認
- GPU/導入済みruntimeから Ollama、llama.cpp/ROCm、llama.cpp/Vulkan の利用可能な構成だけを返す
  RuntimePolicy APIを追加。選択状態、排他/共存、共通idle、チャット出力上限・思考、アシスタント名を保存し、
  llama設定UIのハードコード初期値も保存済み値へ修正
- AMD GPU電力上限を含む後続の詳細設計を`design-model-runtime-assistant.md`へ統合。電力制限機能自体は実装中

検証: backend 183件成功、frontend本番ビルド成功。runtime policyの保存・範囲検証・排他切替を単体テスト済み。

### AMD GPU 静音プロファイル

- 最大VRAMを持つAMD dGPUを選び、実機の電力cap、MCLK/SCLK DPM levelを読んで設定範囲を生成。
  AMD以外および変更非対応GPUではUIを表示しない
- 静音（最小210W・MCLK最大から1段低下）、バランス（255W・clock自動）、フルパワー（既定300W・clock自動）、
  カスタム（実機範囲の電力・MCLK/SCLK上限）をRuntimePolicyとしてサーバー保存。balanced/fullはMCLKを必ずautoへ戻す
- チャット、ワークフロー生成、永続チャット、LLM node、RAG、Ollama手動load、llama.cpp手動startおよび
  systemd `ExecStartPre`の全経路で、モデル起動・生成前に同じpreflightを適用
- `deck.sh service`の初回sudo認証でroot所有の専用helperと限定NOPASSWD sudoersを登録。
  Webプロセスはroot化せず、任意パス/コマンドや範囲外値を受け付けない

実機では静音profileを適用し、power cap 210W、MCLK設定上限1124MHz、負荷中最大875MHzを確認。
81 completion tokenは4.48秒。カスタムSCLK 500MHz制限時は実測最大583MHz、同等生成8.98秒となり、
性能低下を確認後に静音profile（SCLK自動）へ復帰してサーバー保存。1280px/320pxとも全profile・210W・1124MHzを表示し、
横スクロール・console errorなし。backend 191件成功、frontend本番ビルド成功。

### Model画面・llama.cppモデル個別設定の再監査

- ページ名称・説明をOllama固定からLLM Model管理へ変更。選択中runtimeのモデルを共通provider APIから表示し、
  llama.cpp選択時は「GGUF登録」、Ollama選択時は従来の取得/削除を提示
- runtime/backendの選択はシート最上位cardだけに統一。下部の重複backend cardを廃止し、未導入backendの追加と
  現在のGGUFモデル個別設定へ役割を限定
- llama.cppの型付き設定へ、CTX、最大出力、GPU層、K/V別cache量子化、Flash Attention、MTP/draft/ngram、
  MoE CPU配置、batch/ubatch、thread、sampling、mmap/mlockを追加。実バイナリ`--help`に存在する能力だけUI表示
- 自由入力`extra_args`を廃止し、未知キーを422で拒否。model pathはrealpath正規化、許可ルート、GGUF拡張子を検証。
  旧設定は新しい型付き既定値を補いながら移行する
- 保存後、稼働中ユニットの内容が変わった場合だけ再起動して設定を反映。同一設定のloadでは無駄な再ロードを避ける

実機Qwen3.6-27B Q5_K_Mを、新しい`n-predict/batch/ubatch/cache K/V/thread/sampling`引数入りsystemd unitで再起動し、
health 200と短文応答`2`（completion 2 token）を確認。Playwright Chromiumの1280px/320px双方で使用中runtime badgeが1個、
MTP/K/V/MoEが各1箇所、横overflow・console errorなし。複数GGUF catalog/router化は次段の残件。

### llama.cppモデル個別設定の保存422修正（2026-07-16）

- 実サービスで設定保存を再現し、`PUT /models/llama/instances/llama`が422になることを確認。
  GET応答のinstanceをfrontendがそのままPUTし、`selected/loaded/unit/runtime_status/base_url/last_used_at`という
  読取り専用statusフィールドまで含めていたため、backendの`extra="forbid"`に拒否されていた
- frontendは書込み可能な型付き28フィールドだけを明示的に選んで送信。backendの未知フィールド拒否は維持し、
  将来status情報が増えても保存payloadへ混入しない境界にした
- FastAPIの配列形式validation detailをAPI clientで`field: message`へ整形し、数値だけのエラー表示も解消

検証: 修正前は同一instance保存が422で6種の`extra_forbidden`。修正後は実サービスで`n_predict`を
2048→2049へ変更して200・永続化を確認し、200で2048へ復元。専用Playwrightで読取り専用field非送信と
validation message表示を確認。frontend本番build、backend全テスト成功。

### 独立AIアシスタント・ワークフロー生成の再評価

- `/assistant`を独立routeとして追加し、PCサイドバー、モバイル操作シート、command paletteから2step以内で起動。
  ワークフロー画面の既存入口も同じcomponentとして維持
- RuntimePolicyで保存したアシスタント表示名を画面へ反映。server DBの会話一覧を選択でき、新規・改名・削除を追加。
  改名/削除は所有者検証し、削除は利用者指定により確認なしで実行して監査ログへ記録
- 独立routeから実機Qwen3.6-27B + llama.cppで副作用のない最小フローを生成し、10.87秒、品質78/100、
  schema/意味検証済みの開始→結果表示フローとして登録・エディタ遷移を確認。検証用会話/フローは終了後に削除

Playwright Chromiumの1280pxで直接route、会話名server保存、生成・登録を確認。320pxでは会話selectorと全モード、
入力欄が可視範囲内で、横overflow・console errorなし。チャット本文生成の既存実測は0.66秒。

### AIアシスタント全画面表示（2026-07-16）

- 従来のmobile 94dvh bottom sheet / PC中央760px modalを廃止し、他の没入機能と同じ
  `100dvh × 100%`の全画面表示へ統一。背景overlay・画面外click終了をなくし、明示的な閉じる操作へ一本化
- headerへ上部Safe Areaを適用し、既存の下部入力Safe Area、会話・設定・モード・生成機能は維持

検証: frontend本番build成功。Playwright Chromiumの1280x800 / 320x700でdialogがそれぞれ
viewport全域（1280x800 / 320x700）に一致し、横overflow・console errorなし。閉じる操作でhomeへ復帰。

### AIアシスタントのモバイル入力横overflow修正（2026-07-16）

- 320px表示で長い非改行文字列を入力して調査。入力textareaがflex itemの既定値`min-width: auto`のままで、
  mobileでも14pxだったため、iOS Safariでは内容のintrinsic widthによるflex拡張とfocus時auto zoomが重なり、
  右へはみ出して見える条件になっていた
- 入力行を`min-width: 0`で縮小可能にし、textareaへ`width: 0; min-width: 0`を設定。mobile form文字を16px、
  `sm`以上を従来の14pxとして、iOSのfocus zoomを防止した
- dialog、設定、本文、footerへ横方向のcontainmentを追加し、会話名編集はmobileで折り返すよう変更。
  ユーザー入力とLLM応答には`overflow-wrap: anywhere`を設定し、長いURLや非改行文字列もbubble内で折り返す

修正前の再現計測はtextarea `font-size: 14px` / `min-width: auto`。修正後のPlaywright Chromium実測は、
320x700でdocument/body/dialog幅がすべて320px、textarea `font-size: 16px` / `min-width: 0px`、
1280x800でもdocument/body/dialog幅がすべて1280pxで横overflowなし。専用回帰テストを追加した。
Playwright WebKitはホスト側共有ライブラリ不足のため未実行で、実iPhone Safariの最終確認は残る。

### ワークフロー副作用なしdry-run・node metadata

- 従来の「ノード単体テスト」は実executorを呼び、app停止/file書込/Webhook等の副作用を起こし得たため、
  UI既定をexecutorを呼ばない「安全プレビュー」へ変更。既存APIの明示的実テスト互換は維持
- 編集中/保存済みworkflowを永続化や実行なしで静的走査し、構造/意味error、warning、到達wave、
  条件分岐/loop、予定副作用と必要capabilityを返すdry-run APIと結果sheetを追加。secret名/値もredact
- backend executor 35種とcontrol.loopの計36種にversion、side effect、capability、主要config/output型、
  retry/cancel/progress/dry-run対応metadataを追加。LLM catalogで欠落していた5種も統合し、集合差をテスト
- Playwright Chromium 1280px/320pxでfile.write→Webhookを撮影し、書込1/外部通信1の予定表示、
  executor未実行の明記、横overflow 0、console error 0を確認。詳細設計は`design-workflow-dry-run-metadata.md`

## Phase 2 / Phase 4 残件対応（2026-07-15）

- **アプリアイコン**: PNG / JPEG / WebP / SVG（2MB以下）を登録・更新画面からアップロード。実パスをAPIへ露出せず、
  認証・`apps.view` 権限付きエンドポイントから配信。SVGは script / foreignObject / イベント属性 / 外部参照を除去し、
  ラスター画像はマジックバイトを検証。置換・削除・アプリ削除時の後始末を監査対象化
- **ごみ箱**: 通常削除を `data_dir/trash` への移動に変更。ユーザー単位の一覧 / 復元 / 完全削除 / 空にする、
  保持日数・容量上限による古い項目の自動purgeを自己メンテナンスへ統合。元パス復元時も許可ルート検証を再実施
- **再開可能アップロード**: 4MBチャンク、厳密なoffset検証、進捗、中止、同じファイル再選択時の再開、
  完了時のatomic replace。途中ファイルは非公開の `data_dir/uploads` にユーザー所有者付きで保持

検証: `./deck.sh test` 165件成功、フロントエンド本番ビルド成功。悪意あるSVG、偽装画像、実パス非露出、
ごみ箱復元・完全削除、チャンク順序違反・再開・取消を自動テストで確認。実サービスを再起動して health API を確認し、
一時E2Eユーザーでファイル画面・ごみ箱を1280px / 320pxの実ブラウザで確認（横スクロール・console errorなし）。

## 永続電源予約（2026-07-15）

- Webプロセス内 `asyncio.sleep` を廃止し、予約確定時だけ `control-deck-power-schedule.timer/service` を
  systemdユーザーユニットとして生成・`enable --now`。取消時は無効化してユニットと状態を削除
- systemdユーザーtimerによりWebサービス再起動・SSH切断後も継続。`Persistent=false` として、
  PC停止中に期限を過ぎた予約が次回起動直後に誤実行されないようにした。実行ワーカーは一般ユーザーで動き、
  固定引数・配列subprocessでlogindへ要求し、予約実行と成否を監査ログへ記録。実行後はunitを自動回収
- UIは即時 / 15分 / 30分 / 1時間 / 3時間 / 8時間と現在予約の取消に対応

検証: `./deck.sh test` 168件成功、フロントエンド本番ビルド成功。実機で24時間後の検証用timerを作成し、
`Persistent=false` / `active` / `enabled` / 次回実行時刻を確認後、即時取消して `inactive` を確認。
実サービス上の予約ダイアログを1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。
破壊的な電源実行は未実施。

## アプリ別ヘルスチェック（2026-07-15）

- アプリ登録・編集でプロセス存在 / TCPポート / HTTP GET（期待status・本文文字列）/
  ファイル存在を設定可能。ファイルはrealpath正規化と許可ルート・拒否パス検証を強制
- バックグラウンドで15秒間隔に並列確認し、実行中プロセスのチェック失敗を `DEGRADED` として一覧・詳細へ反映
- `POST /apps/{id}/health-check` で手動確認でき、詳細画面に結果と確認ボタンを追加
- HTTP本文は先頭64KBまで、タイムアウトは0.2〜30秒。任意コマンド型は許可コマンド基盤がないため未開放

検証: TCP / HTTP status・本文 / 許可・拒否ファイル / API保存・手動実行 / `DEGRADED` 遷移を自動テストで確認。
実サービスの詳細・編集画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

## LLM runtime provider一般化（2026-07-15）

- Claude作業中の `Models.tsx` を破棄せず、Ollama / llama.cppを同じ「LLMランタイム設定」のタブへ統合
- providerカタログを追加し、Ollama設定URL、llama.cpp設定ポート、LM Studio等の代表ポート、管理アプリの待受ポートを
  OpenAI互換 `/v1/models` で並列検出。provider名・管理対象・導入/稼働状態・モデル一覧を共通形式で返す
- `GET /models/providers` を追加し、従来の `GET /workflows/llm-endpoints` も同じ検出サービスへ移行。
  既存の `base_url` / `models` 形式は維持し、チャット・ワークフローとの互換性を保持
- 設定画面に検出済みproviderとモデル数を表示。Ollama固有のモデル取得・削除・詳細設定は既存APIに分離したまま維持

検証: providerの稼働/停止判定、モデル列挙、一意ID、API、従来ワークフロー検出形式の互換テストと本番ビルドを確認。

### llama.cpp 複数GGUF catalog / instance（再監査補完）

- 従来の単一GGUF設定を互換mirror付きcatalogへ移行。alias・port・実体pathの一意性と最大8件を検証し、
  GGUFごとにhash付きsystemd user unit、起動/停止/health、自動起動、idle unload除外、最終利用時刻を管理
- Model画面からcatalogの選択・追加・改名・設定削除（GGUF本体は保持）を行い、各モデルにCTX、出力token、
  GPU offload、K/V量子化、Flash Attention、MTP/speculative、MoE、thread/batch/sampling/RAM設定を保存
- provider共通health APIと、Ollama/llama.cppを合算する同時ロード上限を追加。チャット、ワークフロー、RAGの
  endpoint利用時に対象instanceを活動中として記録し、誤ったidle unloadを防止
- 詳細設計を`docs/design-llama-multi-instance.md`へ記録し、旧単一設定/APIも互換維持

検証: `./deck.sh test` 206件成功、frontend本番ビルド成功。Playwright Chromiumで1280px/320pxの設定シートを
上端・下端まで撮影し、横overflow 0、console/page error 0。利用可能runtime、AMD GPU、共通load上限、
CTX、K/V cache、MTP、MoEの表示を確認。

### LLM runtime生成・stream・cancel共通契約（2026-07-16）

- provider lifecycleと分離した`LlmRuntimeProvider`生成契約を追加し、Ollama native JSONL、llama.cpp/外部
  OpenAI互換SSEをcontent/thinking/usage eventへ正規化
- workflow生成の非stream処理、永続chat worker、旧chat WebSocketを同じproviderへ移行し、GPU preflight、
  thinking、keep-alive、structured response fallback、秘密値を含めないエラーを統一
- request IDのactive registry、明示cancel、task cancel、WebSocket切断時のHTTP接続cleanupを実装。
  `chat.completion` job取消はprovider cancelを通知してからtaskを停止
- 詳細設計を`docs/design-llm-runtime-chat-contract.md`へ記録

検証: backend 211件成功、frontend本番ビルド成功。実機Qwen3.6-27B + llama.cppで新providerから短文`2`を0.71秒でstream完了。
長文生成を最初のchunkで明示cancelし、0.54秒、active request 0、完了後cancel=falseを確認。
実サービスの統合設定画面を1280px / 320pxの実ブラウザで確認（横スクロール・ログイン後のconsole errorなし）。

### Provider共通モデルライフサイクル

- providerごとに `list/load/unload/delete/pull/configure` capabilityを公開し、共通adapterでモデル情報を
  `id/name/size_bytes/modified_at/loaded/details` に正規化
- `GET /models/providers/{provider}/models` とモデル単位の `load` / `unload` / `DELETE` を追加。
  Ollamaは全操作、llama.cppは設定中GGUFの一覧・起動・停止、外部OpenAI互換は一覧のみ対応
- 未対応の変更操作は `409`、未知provider/modelは `404`。ロード・アンロード・削除はprovider付きで監査
- 既存Ollamaモデル画面とllama.cpp起動・停止UIを共通APIへ移行。既存の固有APIも互換のため維持

検証: `./deck.sh test` 178件成功、フロントエンド本番ビルド成功。実サービスの共通APIからOllamaモデルを取得し、
1280px / 320pxの画面を確認（横スクロール・ログイン後のconsole errorなし）。破壊的なモデル操作は未実施。

### OpenCode オプトインfeature（2026-07-16）

- `./deck.sh feature status/install/enable/disable/uninstall opencode`とfeature registryを追加。通常起動では
  導入・有効化せず、管理prefixと既存外部OpenCodeを区別し、uninstallで外部binary/config/dataを削除しない
- process起動時の有効状態に応じてOpenCode API、lazy frontend route/chunk、PC/モバイルメニュー、command palette、
  workflow `code.agent` executor/catalog/metadataを条件登録。無効時はCSS非表示ではなくAPI/直routeとも404
- projectは既存allowed-root/realpath/symlink検証を通し、promptとOpenAI互換provider設定を600権限のjob別一時ファイルへ
  分離。OpenCodeはsystemd user transient unitで起動し、job cancelでunit停止、出力上限と一時ファイル後始末を強制
- 独立画面からanalyze/implement/fix/test/review、endpoint/model/projectを設定でき、実行は既存job streamで追跡する

実機では外部OpenCode 1.17.15を明示enableし、llama.cpp（Qwen3.6-27B、CTX 16384）経由のrepository分析jobが
6 eventsで成功。完了後に既定無効へ戻し、`enabled_features=[]`、API/直route 404、node catalog非掲載を確認した。
Playwright Chromiumの1280px/320pxで横overflow 0、console/page error 0、healthy表示と全5操作を撮影確認。
外部実体保持、PATH差、配列argv、symlink拒否、cancel unit停止、一時ファイル消去を回帰テスト化した。

### ワークフローノードcatalog・並列map完成（2026-07-16）

- 完了表記を再評価し、`control.loop.parallel`が共有contextを上書きしてitem/indexを競合させる問題を再現。
  iterationごとの分離context、入力順`results`、最後のbody出力の互換mirrorへ直し、1〜5並列mapを実装
- `data.transform`へJSON parse/get/set、Draft 2020-12 Schema検証、CSV相互変換、`file.glob`へallowed-root・
  symlink脱出防止付き検索、`ai.utility`へOpenAI互換embedding、rerank、LLM judgeをoperation統合
- health checkは既存`http.request.expect_status`と重複するため再実装せず、既存UI/説明を維持。file.op/C++ buildと
  command出力で見つけたcatalog/required key/output metadataの実装不一致も実キーへ修正
- node進捗ContextVarをtask単位に分離し、loop/data/glob/AI補助のprogressをlive contextへ公開。
  backend catalogを表示可否の正として、検索、カテゴリ、localStorageお気に入り、利用可能のみ既定ON、未導入確認を追加

検証: backend 224件成功、frontend本番build成功。実サービスAPIでSchema検証を実行し、catalog 39種と新3種の
progress対応を確認。Playwright Chromium 1280px/320pxで検索・お気に入り・絞込・未導入表示を撮影し、
横overflow 0、console/page error 0。詳細設計は`docs/design-workflow-node-catalog.md`。

### 全機能後のWeb軽量化再測定（2026-07-16）

- ダッシュボード30秒でmetrics WS 1接続/15 frame、`GET /apps` 2回、overview初回1回。外向き高周波pingなし
- 1280px実ブラウザで横overflow 0、console/page error 0。ブラウザ切断後のservice cgroup 10秒CPUは0.46%
- GPU collectorは引き続き`sysfs-amdgpu`で、周期的な`amd-smi metric` process起動を行わない

## リモートデスクトップの環境互換性メモ（2026-07-12、重要）

- **Control Deck 側は完全動作**: WS トンネル・認証・guacd ハンドシェイク・ビューアは実機で確認済み
  （guacd が接続を受理し ready/size/image/cursor を配信）
- **ブロッカー**: Ubuntu 24.04 同梱の guacd 1.3.0（FreeRDP 2.11.5）は GNOME Remote Desktop 46
  （FreeRDP 3 系）と RDP ネゴシエーション非互換（全 security タイプで "wrong security type"）
- **対処**: ヘッドレスは **xrdp**（FreeRDP2 互換）を使う方式へ変更。`enable-desktop`（既定ヘッドレス）は
  xrdp を導入し、システムアカウントで PAM 認証、接続時に新規セッションを作成。GNOME RD の RDP は解放
- **接続フォームに security 選択を追加**（any/nla/tls/rdp）。Windows は nla、xrdp は any
- 既知の注意: xrdp + GNOME は「同一ユーザーが同時に 1 セッションのみ」の制約あり。画面を閉じた
  ヘッドレス運用（コンソール未ログイン）を想定

## この PC のヘッドレスデスクトップ操作（2026-07-12、ユーザー要望）

- **`./deck.sh enable-desktop`**（既定ヘッドレス）: GNOME Remote Desktop を `grdctl --system` で設定し、
  この Ubuntu を Web から操作可能にする。TLS 証明書を openssl で自動生成、RDP 認証情報を対話入力、
  guacd を導入、Control Deck に `127.0.0.1:3389` への接続「この PC（headless）」を自動登録
- **ヘッドレス（既定）**: 接続時に仮想セッションを作成（物理画面不要、画面を閉じた運用向け）。
  **リモート接続を有効化するまで仮想デスクトップは作られない**（enable-desktop を実行し、かつ
  クライアントが接続したときのみ）
- **`--active`**: 現在のログインセッションを共有（画面ミラー）。`grdctl`（ユーザー daemon）
- **`./deck.sh disable-desktop`**: 無効化
- 接続登録は `app.cli register-local-desktop`（パスワードは環境変数経由で argv に載せない、暗号化保存）
- セキュリティ: RDP:3389 は Control Deck 経由での利用を前提。外部はファイアウォール/VPN で遮断を案内

注: enable-desktop はシステム状態変更（サービス有効化・ポート開放・パスワード設定）を伴うため、
ユーザーが明示実行する。アプリ側が勝手に仮想セッションを作ることはない。

## Phase 6 リモートデスクトップ（2026-07-12）

- **guacd トンネル**: WebSocket（guacamole-common-js）↔ guacd(TCP:4822) を橋渡し。接続開始時の
  ハンドシェイク（select → args → size/audio/video/image → connect）をサーバー側で実施し、
  以降は raw ストリームを双方向パイプ（guacamole-lite 相当を Python で実装、外部依存なし）
- **接続管理**: RDP / VNC / SSH の接続 CRUD。パスワード等の機微パラメータは Fernet 暗号化保存、
  API 応答には含めない（has_password フラグのみ）。RDP は ignore-cert / display-update を既定化
- **ビューア**: guacamole-common-js（遅延ロード）。マウス + タッチパッド（タップ=クリック・長押し=右クリック）+
  キーボード、Ctrl+Alt+Del、画面リサイズ追従、モバイルはソフトキーボード呼び出し
- **導入**: `remote_desktop.enabled: true` のとき deck.sh が guacd の apt 導入を試みる。
  未導入時は UI に案内を表示し接続ボタンを無効化
- **バックアップ修正**: sqlite3 CLI 非依存に変更（venv Python の sqlite3 backup API で整合スナップショット）

検証: pytest 79 件成功（命令エンコード/パーサ、モック guacd での select→args→connect ハンドシェイク、
接続 CRUD、パスワード暗号化非漏洩）。Playwright で接続一覧・追加フォームを PC/モバイル確認。
ライブ接続は guacd + 実ホストが必要なためこの環境では未実施。

## バックアップ / リストア（2026-07-12、Phase 7）

- `./deck.sh backup [出力先]`: DB / 設定 / 暗号鍵 / RAG / アプリの systemd ユニットを tar.gz に。
  sqlite3 があれば WAL checkpoint 後にコピー（ログは容量のため既定除外）
- `./deck.sh restore <ファイル>`: 復元前に自動退避コピー、確認プロンプトつき、daemon-reload
- `GET /system/backup`（settings.manage）: 設定ページの「バックアップ」からブラウザで DL 可能
- 検証: backup→DB 改変→restore で復旧＋退避コピー生成を確認。DL API も 200/gzip 確認

## PWA 対応（2026-07-12、Phase 7）

- manifest.webmanifest（standalone、テーマ色、192/512/maskable アイコン）+ apple-touch-icon /
  apple-mobile-web-app メタ。ホーム画面追加・フルスクリーン起動に対応
- Service Worker（sw.js）: **アプリシェル（HTML/JS/CSS/アイコン）のみキャッシュ**。
  `/api/`・WebSocket・認証は一切キャッシュしない（機密を Service Worker に保存しない方針）。
  アセットは cache-first（ハッシュ付き名）、ナビゲーションは network-first + オフライン時シェルフォールバック
- 本番ビルドのみ SW 登録（開発時は登録しない）。アイコンは Chromium で SVG ロゴから生成
- TOTP リセット: `./deck.sh reset-totp <ユーザー名>`（`--all` で全員）でロックアウト復旧可能

検証: SW 登録・アクティブ化・manifest 読込を Playwright で確認。オフライン再読み込みでアプリシェルが
起動することを確認。

## TOTP 二要素認証（2026-07-12、Phase 7）

- 有効化: setup（QR=SVG data URI、Pillow 不要）→ 6 桁 verify → リカバリーコード 10 個を 1 回表示
- ログイン 2 段階: TOTP 有効時は `two_factor_required` → コード入力（6 桁 or 使い捨てリカバリー）
- 無効化はコード確認つき。シークレット/リカバリーコードは Fernet 暗号化保存、使用時に消費
- `require_totp_for_admin` で管理者に推奨バナー。bootstrap に SQLite 軽量マイグレーション追加
- 検証: pytest 71 件、Playwright + pyotp で全フロー E2E

## アラート通知（2026-07-12、Phase 3 残り完了）

- **ルール**: メトリクス（CPU/RAM/GPU/VRAM/CPU温度/GPU温度/ディスク使用率/アプリ停止）× 演算子（>/≥/</≤）
  × しきい値 × 継続時間（sustained）× クールダウン。アプリ停止は対象アプリ指定
- **通知チャンネル**: Discord / Slack / 汎用 Webhook。URL は Fernet 暗号化保存・表示時マスク・テスト送信可
- **評価ループ**: 15 秒間隔で評価。継続時間を満たすと AlertEvent 発火＋通知、条件解消で resolved。
  ウォッチドッグの心拍対象にも追加
- **UI**: 設定に通知チャンネル / アラートルール管理、ダッシュボードにアクティブアラートバナー
- **必要ソフトの自動導入**: deck.sh に tesseract / tmux の apt 導入（passwordless sudo 時）と
  Playwright ブラウザ（Chromium）自動導入を追加

検証: pytest 62 件成功。E2E で webhook 受信サーバーへの発火通知＋テスト送信を確認。

## ワークフロー拡張 v2（2026-07-12、ユーザー要望）

- **ノード追加**（全 25 種）: ループ（回数 / foreach、body/done 2 出力、`{{ID.item}}`/`{{ID.index}}` 参照）、
  変数セット、文字列操作（大小変換 / 置換 / 正規表現抽出 / 分割 / JSON 抽出 / テンプレート）、
  Markdown→HTML、ファイル読込 / 出力（追記可）/ 操作（copy/move/delete/mkdir）、
  LLM 生成（OpenAI 互換 = Ollama/vLLM/llama.cpp/OpenAI）、Web スクレイピング（CSS セレクター）、
  ブラウザ操作（Playwright）、OCR（tesseract）、Wake-on-LAN、
  SSH 実行（鍵認証 BatchMode、host 検証）、Git 操作（サブコマンド許可制）、C++ ビルド（CMake/Make）、
  Python 実行（**初期無効**、`security.allow_arbitrary_commands` で許可、venv python の -I -c 実行）
- **安全性**: すべて shell=False の配列実行。ファイル系は許可ルート検証を通す。SSH host / Git サブコマンドは
  ホワイトリスト。任意シェル文字列ノードは非提供
- **エディター刷新**: アイコン付きノード + カテゴリカラーバー、実行状態リング、ドットグリッド背景、
  ミニマップ、矢印マーカーエッジ、ループの反復/完了ハンドル
- **カスタムノード / スニペット**: 選択ノード群をスニペットとして localStorage 保存 → パレットから再挿入
- **ワークフロー入出力**: 定義を JSON でエクスポート / インポート（他環境への持ち運び）

検証: pytest 56 件成功（v2 ノード 13 件: 文字列 / 変数チェーン / Markdown / ファイル IO / WOL /
Git 許可制 / SSH host 検証 / Python 無効 / ループ foreach・count / スクレイピング）。
E2E で「foreach ループ → 大文字化 → ファイル追記」を実行し APPLE/BANANA/CHERRY 出力を確認。
Playwright でダーク/ライト・PC/モバイルのエディターとパレットを確認、横スクロール 0・エラーなし。

### RAG 構築 / DB 操作ノード（2026-07-12 実装）

- **db.query**: SQLite（許可ルート配下のファイル）または任意 SQLAlchemy URL（PostgreSQL 等）へ SQL 実行。
  名前付きパラメータ（`:id`）でバインド。先頭が SELECT/INSERT/UPDATE/DELETE/CREATE 等の SQL のみ許可、
  SQLite パスは許可ルート検証。SELECT は最大 500 行を dict で返す
- **rag.build**: テキスト（またはファイル）をチャンク分割 → OpenAI 互換 `/v1/embeddings`（Ollama の
  nomic-embed-text 等）で埋め込み → コレクション別 SQLite（data_dir/rag/{name}.db）へ保存
- **rag.query**: 質問を埋め込み、numpy コサイン類似度で top-k チャンクを取得。`{{ID.context}}` を
  LLM ノードへ渡すことで RAG パイプラインを構成（依存はベクトル DB 不要、numpy のみ）

検証: pytest 69 件成功。E2E で DB クエリ（テーブル作成→挿入→カウント）、RAG（フェイク埋め込みで
build→query マッチ）を実機ワークフローで確認。

## 自己メンテナンス / ウォッチドッグ（2026-07-12、ユーザー要望で追加）

- **systemd ウォッチドッグ**: `Type=notify` + `WatchdogSec=30` + `NotifyAccess=main`。
  起動完了時に READY=1、内部ヘルスチェック（DB 接続 / メトリクス収集の鮮度 / スケジューラー心拍）が
  正常な間のみ 15 秒間隔で WATCHDOG=1 を送信。ハング・内部異常時は systemd が SIGABRT → 自動再起動
- **自己メンテナンスループ**（起動 5 分後 + 1 時間間隔）:
  ログローテーション（copytruncate + gzip、`rotate_size_mb`/`rotate_generations`/`retention_days`、
  仕様 §11.3 対応）/ 期限切れセッション purge / 監査ログ保持（`audit_retention_days` 既定 180 日）/
  SQLite WAL checkpoint + optimize / ディスク残量自己点検（10% 未満で警告）
- **自己状態 API/UI**: `GET /system/self-status` + システムページ「Control Deck 自己診断」セクション

検証: pytest 43 件成功（ローテーション世代管理 / purge / ヘルスチェック / sd_notify フォールバック）。
実機で SIGSTOP によるハング模擬 → 30 秒で `Watchdog timeout` → SIGABRT → 自動再起動 → 復旧を確認。

## Phase 5 実装内容（2026-07-12）

- **エンジン**: ノードグラフ実行（トリガー → 逐次 + 条件分岐）。ノード別タイムアウト、
  ステップ上限 / ループ防止、実行キャンセル、ノードごとの入出力・エラー・時刻を保存
- **ノード**: トリガー（手動 / 間隔 / 毎日 / cron）、アプリ起動・停止・再起動・状態取得、
  HTTP リクエスト（期待ステータス検証）、条件分岐（eq/ne/gt/lt/contains、真偽 2 分岐）、待機、
  Webhook 通知（汎用 / Discord / Slack）、ファイル存在確認（許可ルート検証を通す）。
  テンプレート `{{ノードID.フィールド}}` で前段出力を参照可能。**任意シェル実行ノードは提供しない**（§20.6 安全モード）
- **スケジューラー**: 30 秒間隔で有効ワークフローの間隔 / 毎日 / cron（croniter）トリガーを評価
- **API**: workflows CRUD / run / enable / disable、workflow-executions 一覧・詳細・cancel（すべて RBAC + 監査）
- **UI**: 一覧（実行ボタン + 前回結果 + スケジュールトグル）、React Flow エディター（遅延ロード、
  カスタムノード、条件ノードは真/偽 2 ハンドル、ノードパレット + 設定ボトムシート、モバイル対応）、
  実行履歴シート（ノードごとの出力 JSON 表示、実行中は自動更新）

### UI テーマ / ロゴ（同日、PR #4）

- モード（システム / ライト / ダーク）+ アクセント 6 色 + OLED 完全黒。localStorage 永続化
- スライダーモチーフの SVG ロゴ（アクセント色連動、favicon 含む）

検証: pytest 37 件成功（定義検証 / テンプレート / 条件分岐グラフ / API CRUD+実行 / viewer 権限 /
スケジュール判定）。E2E で HTTP ヘルスチェック → 条件分岐 → true 側のみ実行を確認。
Playwright（1280 / 390px）でエディター・パレット・設定シート・実行履歴を確認、横スクロール 0・エラーなし。

## Phase 4 実装内容（2026-07-12）

- **ファイル**: 許可ルート限定（realpath + commonpath + 拒否リスト ~/.ssh 等）。一覧 / アップロード
  （複数・D&D・上書き確認・サイズ上限）/ ダウンロード / プレビュー（画像）/ テキスト編集
  （Monaco 遅延ロード、CDN 不使用、Ctrl+S 保存）/ mkdir / rename / copy / move / 削除（確認 + 監査）
- **ターミナル**: PTY + WebSocket。tmux があれば永続セッション（cdterm-*）、なければプロセス内 PTY
  フォールバック（切断→再接続でバッファリプレイ）。xterm.js 遅延ロード、モバイル全画面 +
  補助キーバー（Esc/Tab/Ctrl/矢印/^C/^D）、visualViewport 対応、リサイズ同期、監査記録
- **運用**: 単一エントリースクリプト `./deck.sh` へ統合（venv / Node 依存 / ビルド / 設定 / linger /
  管理者の不足を自動判定して整えてから起動。旧 scripts/* は互換ラッパー化）
- **修正**: ログインレート制限を「失敗のみカウント」方式へ（正規ユーザーの連続ログインで誤制限しない）

検証: pytest 31 件成功（ファイル API roundtrip / トラバーサル / symlink 脱出 / viewer 権限 /
ターミナルライフサイクル / ブルートフォース制限）。実機 E2E（アップロード→ダウンロード、
symlink→/etc が 403、WS ターミナルで echo 実行→再接続リプレイ）。Playwright で 1280/390/320px
確認（横スクロール 0、コンソールエラーなし、モバイル全画面ターミナル + キーバー表示）。

### ターミナルのモバイルキーボード・長文履歴再監査（2026-07-15）

- mobile software keyboardによるvisual viewportの縮小・移動へ、ターミナルroot自体を追従させた。
  bodyを固定せず背景scrollだけを止め、browserの自動panとの二重移動、入力位置や画面の欠落を解消
- xtermとtmuxの履歴を100,000行へ統一。接続時にtmuxの全履歴を最大16MiBでsnapshot再生し、
  再接続中の出力も復元する。上限超過時は無言で消さず切り詰め通知を表示
- attach直後の端末resetがsnapshotを消していた順序不具合を修正し、初期化→browser reset→snapshotの順へ統一。
  session IDも8桁hexへ限定し、PCヘッダーに全文コピーを追加
- `deck.sh`のservice登録判定が`pipefail`と`grep -q`でSIGPIPE終了し、登録済みでもforeground起動を試みる場合が
  あったため、`systemctl --user cat`による判定へ変更

検証: 実tmuxへ10,000行を出力し、Playwright Chromiumの1280px/320px双方で先頭・末尾を確認、末尾重複1回。
320pxでvisual viewportを`top=180 / height=300`へ移動してroot・補助キーバーが同範囲内、入力textareaが透明、
bodyが`position: static`のままであることを座標・computed style・撮影で確認。詳細は
`docs/design-terminal-mobile-history.md`。

### ターミナルのモバイル1行操作・touch履歴追従（2026-07-16）

- xterm.js 6の独自scrollbarがmobile touch dragを履歴位置へ反映しないことを実ブラウザで再現。
  terminal面の単指縦dragをcell単位の`scrollLines`へ変換し、指移動中に過去出力を追従表示するよう修正
- モバイル補助操作列は高さ40px・`flex-nowrap`の1行へ固定し、横scrollbarを非表示化。
  空の2段目を作っていたSafe Area paddingを撤去し、履歴専用ボタンを置かず画面scrollへ統一
- `visualViewport.scroll`時は座標同期だけにし、hostの行列数が変化した場合だけPTY resize。
  keyboard/IME入力中の無駄なreflowを止め、長文入力の表示変動を抑制
- tmux初期描画の非同期`Terminal.write()`を同期`reset()`が追い越し、履歴境界へ現在画面が混在する不具合を特定。
  初期描画→reset→snapshot→streamをwrite callbackのPromise chainで直列化し、完了扱いだった履歴重複を再修正
- tmux captureとbrowser全文コピーでsoft wrapを論理行へ復元し、画面幅由来の改行がコピー内容へ混ざらないよう修正

検証: 実tmux sessionへ300行を出力し、Playwright Chromium `320x700 / hasTouch`で指dragにより
viewportYが263→250へ移動し、251〜291行を欠落・重複なく連続表示。全13補助ボタンのtop/bottomが
667/694pxで一致し、toolbarは40pxの1行、横scrollbar表示なし。1280x800ではmouse wheelで
253→250、251〜300行を連続表示。双方console error 0。backend 224件成功、frontend本番build成功。

### ターミナル処理中入力の描画競合（2026-07-16）

- `\r\x1b[K`で80ms更新するWorking表示中に文字入力し、320pxでterminal面の黒化と表示行分離を再現。
  PTY入力・処理結果は保持されており、空になった旧native viewportのmomentum-scroll合成layerが主因と特定
- 旧viewportの合成layerを無効化し、全scrollイベントの強制refreshを廃止してxtermの差分描画へ戻した。
  viewport下地もtheme背景へ統一し、renderer更新間の黒い下地露出を防止

検証: 実tmuxでWorkingを80ms更新しながら途中入力。Playwright Chromium 320x700では入力結果を保持し、
touch履歴位置44→31、1280x800ではwheel履歴位置3→0へ移動。黒化・行分離・横overflow・console errorなし。

### ターミナルIME確定時の行位置ずれ（2026-07-16）

- 320pxでkeyboard相当のviewport縮小中、文字確定ごとに全行が3px上へずれ、再表示で直る現象をidle/Working中に再現
- xtermの端数cellがhost content高を3px超える状態で、`overflow: hidden`のhostがIME用textareaを表示するため
  `scrollTop=3`へ自動scrollされていた。hostをscroll containerにしない`overflow: clip`へ変更
- keyboard開閉を4回反復し、各入力確定前後でhost scrollTop=0、先頭行top=43px、末尾行top=358pxを維持。
  閉じた後は末尾行top=643pxへ正常復帰し、Working中に入力した`ZZZZ`の実行結果も保持。
  mobile touch履歴48→35、PC wheel履歴78→75、双方console errorなし。backend 224件・frontend本番build成功

### iOS Visual Viewport・xterm fit・PTY resize安定化（2026-07-16）

- `FitAddon`が直接の親paddingを寸法から引かないため、hostの左右8px・上4px分だけ行列を過大算出していたことを特定。
  装飾paddingを外側wrapperへ分離し、xtermの直接の親を無padding・非scroll containerへ変更
- keyboard animation中にvisual viewportとResizeObserverの中間寸法を逐次反映していた処理を、最新世代だけを
  2 RAF + 50ms後に適用する単一schedulerへ統合。確定geometryのfit/refreshはPTY write queueと直列化
- 0/極小・非表示geometryを除外し、PTY通知をrows>=3 / cols>=10、同一接続内の重複なしに制限。
  再接続時は最終有効寸法を再送し、backendもrows 3〜500 / cols 10〜1000へ正規化して重複ioctlを抑止
- visibility復帰、pageshow、window/visual viewport resize、ResizeObserverを統合し、全listener・RAF・timer・WS handlerをcleanup。
  opt-in診断は`localStorage['control-deck:terminal-geometry-debug']='1'`でのみ有効

検証: Playwright Chromium 320x700で80ms未満のWorking出力中にkeyboard相当の410/700px切替を10回実施。
開時21行・閉時41行、全行15px等間隔、host padding/scrollTop=0、xterm instance=1、入力10文字保持、
無効PTY resize 0、同一接続内重複0、touch履歴89→76、再mount後41x38再同期、console errorなし。
PC 1280x800↔900x600を5回反復し、50/37行、cols 160/112、入力保持、wheel履歴137→134、console errorなし。
backend 225件成功、frontend本番build成功。

### iOS IME composition・geometry・TUI描画同期（2026-07-16）

- PR #73の通常fitに残っていた全行`refresh()`と、IME状態を知らないroot座標/寸法更新が、iOS未確定文字のtextarea座標と
  fullscreen TUI再描画を別時点へ動かす競合を根本原因として再監査
- `TerminalWriteQueue`、`TerminalImeController`、`TerminalGeometryController`へ責務を分離。composition開始から終了後2 RAFまで
  resize/refresh/root geometry/PTY resizeを禁止し、保留変更を単一schedulerで最終geometryへ1回だけ反映
- 通常fitから全行refreshを削除。renderer復旧はpageshow/visibility/再接続時にも実測不一致がある場合だけ、同一世代1回・1秒cooldownで実行
- size/position/renderer/connection invalidation、2 RAF + 50ms、write queue投入を集約。DOM read/writeをframe分離し、position-only・同一寸法をno-op、
  queue滞留最大1件、touchmoveのcell計測をgesture開始1回へ削減。scrollback 100,000と既存操作は維持
- opt-in診断へIME event、textarea/cursor/各layout rect、fit世代、処理回数、queue滞留、Long Taskを追加。通常時は詳細object/DOM診断/consoleを生成しない

自動検証（Playwright Chromium mobile 320x700、実tmux）: size request集中時はfit request 28→実fit 1、resize 1、PTY 1、refresh 0、
queue最大1、Long Task 0。composition中100件はfit/resize/refresh/PTY/DOM readすべて0、終了後fit/resize/PTY各1、textarea 1。
50ms Working 200回 + keyboard開閉10回で出力/入力欠落なし、refresh 0、queue最大1、textarea 1、controller listener 13で固定。
helper 40px、layout合計誤差1.5px以内、screen/helper非重複を確認。PC 1280x800でwheel履歴、全文copy、再mount 0→1 textarea、
console error 0を確認。10分soakは730周期（IME/開閉/pageshow/20周期ごと再mount）成功、終了時heap 11.9MB→11.9MB、
geometry task滞留0・最大1、refresh 0、Long Task 0、textarea 1、controller listener 13。backend 225件、frontend build、
Playwright通常5件成功（soak 1件は通常skip）。物理iPhone Safari/PWAの日本語候補UI・開閉10回・background/回線再接続録画は
環境外のため**実機確認待ち**。
- マージ後の座標実測で、最終resize後もxterm 6.0.0のtextareaだけ旧top=643pxに残ることを追加検出。xterm最新版はcursor move時だけ
  textareaを同期するため、最終geometry完了後に内部と同じセル座標式を1回適用する追補修正を実施。composition中はtop=643px、
  host bottom=660px、helper top=660pxを固定。終了後はtextarea top/bottom=373/388px、host bottom=390px、helper top=390pxへ同期し、
  textarea 1、rows/cols=23/38、terminal runtime console error 0を実測。composition/PC回帰2件も成功
- その後の物理iPhone報告で、keyboard表示中の通常PTY文字まで`Working`が文字単位・複数座標へ分散する重大回帰を確認。
  Chromiumでは再現せず、PR #75で追加したscreen外寸近似によるhelper textareaの`left/top/width/height/lineHeight`直接変更と、
  composition flush後の無条件focusがiOS Safariのviewport/合成layerを再駆動する最有力要因と判断した。原因を混ぜない緊急対応として
  PR #75の独自同期、専用completion RAF、無条件focusだけを撤去し、PR #74のcomposition lock、単一geometry scheduler、write queue、
  通常fit refresh 0、touch/copy/reconnect/100,000行scrollbackは維持した。
- ロールバック後の自動検証: frontend build成功、backend 225件成功、Playwright Chromiumはmobile 320pxのcomposition/geometry、
  xterm DOM row高さ・間隔一定かつtransformなし、50ms Working 200回 + keyboard相当10往復、desktop wheel/copy/remountの5件成功
  （10分soakのみ通常skip）。ControlDeckによるcomposition後textarea inline style変更0、full refresh 0。物理iPhoneでの
  英字/日本語/削除/Working/keyboard開閉10回は再確認待ちとし、この時点ではresize ACKやroot top/left変更を追加しなかった。
- PR #76後の物理iPhoneでも通常PTY文字の分散、placeholder二重化、空白画面化が再現したため、保留していた世代付きPTY resize
  transactionを実装。frontendはconnection/resize generation付き要求を送り、backendは`TIOCSWINSZ`成功後だけACKする。
  backendのbinary/ACK送信を接続単位lockで直列化し、frontendはACK後に受信した最初のPTY frameを単一write queueで描画完了してから、
  保留inputを`term.onData()`受信単位のFIFOで解放する。出力しないshellだけ125ms fail-safe、上限256 chunk/256KiB、再接続/disposeで
  旧queue破棄。同一geometry、position-only、force syncはbarrierを作らない。
- fixed rootへVisual Viewport offsetTop/Leftを再適用する二重panを撤去。size変更だけroot寸法とfitへ反映し、transaction中の新geometryは
  最新1件へ集約。resize完了後にbuffer/DOM cursor周辺だけを比較し、不一致時のみ同一resize世代1回・cursor前後1行をrefreshする。
  通常input/Backspace/WorkingではDOM比較・refreshなし。PTY制御要約、世代時系列、tmux/PTY size、明示buffer/DOM snapshotを
  opt-in最大300件診断へ追加し、通常時は本文decode/DOM計測/subprocess診断を行わない。
- 検証: backend 226件成功、frontend build成功。Playwright Chromium 320pxは9件成功（10分soak 1件skip）。古いACK破棄、ACK前
  3 input chunk（絵文字含む）保持、ACKだけでは未解放、次PTY write callback後FIFO解放、再接続世代で旧input破棄、position-only/
  同一geometry barrier 0、placeholder buffer/DOM各1・mismatch 0、xterm/textarea各1を確認。Working 50ms×200回 + keyboard相当10往復は
  resize/PTY/ACK各18、timeout 0、full refresh 0、geometry queue最大1、Long Task 0、出力/入力欠落0。PC wheel/copy/remountも成功。
  Playwright WebKit 26.5は取得済みだが、ホストに`libevent-2.1-7t64`、`libavif16`、`libwoff1`がなく、sudoersも対話認証を
  要求するため、この環境では起動不可。物理iPhoneの縦横回転・background復帰を含む10セットは新transaction反映後の確認対象で、
  Chromium成功と区別して未完了扱いとする。
- 実サービスの世代診断で根本原因を追加確定。keyboard相当resize要求`38x23`に対し、従来はACK/125ms後もPTY=`38x23`、
  tmux client/window=`38x41`で、独立process groupの`tmux attach-session`へSIGWINCHが伝播していなかった。ioctl成功後に
  ControlDeck所有attach process groupへ明示SIGWINCHを送るよう修正し、ACK時点・transaction後probeともPTY/client/window=`38x23`、
  `window-size=latest`一致を実測。さらにlocal xterm resizeをbackend ACK前からACK handlerのwrite queue commitへ移し、ACK前の旧size
  PTY frame→local resize→ACK後SIGWINCH frameの順を保証した。これにより旧41行前提のANSI cursor/Working出力を23行xtermへ
  解釈させる世代跨ぎを防止する。
- PR #77後の「keyboard開閉で全履歴再読込に見える」現象を接続診断で分類。実ControlDeck Webの320px keyboard相当開閉10回では
  WebSocket created/close増加0、history_reset増加0、replay増加0であり、接続維持中の実size変更18回に対するtmux/TUIの
  SIGWINCH全画面再描画だった。一方、意図的なWebSocket切断では従来、新attach作成と`history_reset + capture-pane全量`が必ず発生した。
- 再接続を`clientInstanceId + connectionGeneration + lastSequence`による差分resumeへ変更。同じbrowser instanceのtmux attachを
  切断後30秒保持し、4MiB/4096 chunkのsequence journalへ切断中も1回だけ記録する。journal範囲内は既存xterm bufferを維持して差分だけ、
  範囲外/backend再起動/完全reloadだけ`resume_reset_required`後のbounded snapshotへfallbackする。接続状態を
  DISCONNECTED/CONNECTING/INITIAL_REPLAY/RESUMING/LIVE/CLOSEDで管理し、LIVE前inputをFIFO保持。新世代接続後に旧socketのfinallyが
  cleanupを予約する競合も防止した。
- 検証: terminal backend 12件・backend全231件成功、frontend本番build成功。実ControlDeck Web Chromiumは14件成功
  （10分soak 1件skip）。keyboard開閉10回は接続1、
  history_reset 1→1、replay 1016B→1016B、full refresh 0、geometry queue最大1、Long Task 0。Working 50ms×200回中も接続/replay増加0。
  意図的切断1回はresume_ready 1、history_reset増加0、切断中2 chunkを順序どおり差分描画し、入力欠落・重複0。journal範囲外は
  reset/fallback各1、完全reloadはinitial replay 1、session切替は履歴混在0。
- PR #78で追加した`crypto.randomUUID()`がiOS Safariの非secure HTTP contextで未定義となり、XtermViewのeffect初期化が例外終了する
  回帰を修正。共通`createUuid()`を`src/lib/clientId.ts`へ分離し、randomUUID→getRandomValuesによるUUID v4→時刻・
  Math.randomの一時IDの順にfallbackする。全経路でbackend契約を満たし、crypto自体が未定義でも例外にしない。IDはXtermViewのeffectごとに1回生成し、
  同一mount内のWebSocket再接続では維持する。Playwright runnerの生成試験4件（cryptoなし1000件一意性を含む）、frontend本番build、
  backend全235件が成功。サービス再起動後の`/api/v1/health`は127.0.0.1とTailscale HTTP `100.82.8.44:8765`の双方で成功した。
  認証付きChromium 16件成功（soak 1件skip）。randomUUIDを無効化したHTTP上のmount・再接続維持・画面例外なしに加え、
  320px keyboard 10往復、差分resume、desktop wheel/copy/remountも確認。物理iPhone Safari/PWA確認は未完了扱いとする。
  Service Worker cacheをv14へ更新し、旧shell/assets cacheをactivate時に削除する。

- Webターミナルの長文paste欠落を修正。接続前FIFOとresize FIFOの256 chunk/256KiB上限による無言破棄を廃止し、pasteを通常キー入力から
  分離した`TerminalInputController`へ移行。全文をUTF-8化して8KiBずつ、未ACK 1 chunkで送信し、`bufferedAmount`、LIVE状態、resize barrierに
  基づきpause/resumeする。backendはinput control+binaryを検証し、PTY全量書込み後だけsequence ACKを返す。ACKはclient streamへ5分/8192件
  保持し、再接続時の同一sequence再送は二重書込みせず再ACKする。stale世代ACKは無視し、session切替/disposeは残りをcancelする。
- `TerminalConnection.write()`は単発`os.write()`から、部分書込み・InterruptedError・non-blocking書込み可能待ち・0 byte異常を扱う全量書込みへ変更。
  長文表示は32KiB以上だけ100ms throttleの進捗、キャンセル、失敗時再試行を表示。xterm標準と同じCR変換とbracketed pasteをpaste全体へ1回だけ適用。
  opt-in診断はpaste/chunk/sequence/文字・byte数/累積量/世代/bufferedAmount/hash/マスク値だけを記録し、本文は保存しない。
- 検証: backend全235件、frontend本番build、controller/ID Playwright 7件成功。実サービス再起動後health成功。認証付きChromium実サービスで
  100KB ASCII（102422B）、300KB ASCII（307232B）、日本語+絵文字（104540B）をraw PTY受信機の長さ+SHA-256で完全一致確認し、欠落・重複・
  replacement character 0。100KB送信中の320px keyboard geometry 10往復+resize barrier、300KB送信中のWebSocket切断+差分resumeも同じhashで完了。
  PC幅のwheel/copy/remount、session切替、従来resize FIFOも成功。物理iPhone Safari/PWAでのbrowser pasteイベント分割数だけ未計測。

## 実装済み機能

### バックエンド（FastAPI + SQLite WAL）
- 認証: Argon2id / サーバー側セッション（HttpOnly + SameSite Cookie、DB はトークンハッシュのみ）/
  CSRF（X-Requested-With 必須）/ ログインレート制限 / セッション一覧・失効
- RBAC: administrator / operator / viewer。REST・WebSocket 双方で権限依存性を強制
- 監査ログ: ログイン成功・失敗 / アプリ登録・編集・削除・起動・停止・強制終了 / ログ削除 / 電源操作
- アプリ管理: python_script / shell_script / executable / systemd_service（ユーザーユニット）
  - systemd ユーザーユニット生成（`cdapp-{id}.service`、引数エスケープ + インジェクション対策 + StartLimit 再起動ループ検出）
  - start / stop / restart / kill、8 状態マッピング、PID / 稼働時間 / CPU / RAM 取得
  - 環境変数は Fernet 暗号化保存、表示時は秘密キーをマスク
  - Python インタープリター自動検出 / プロジェクト検出（提示のみ）
- ログ: stdout / stderr の append 保存、tail / ダウンロード / 削除 / WS ストリーム
- 監視: psutil + GPU プロバイダー（amd-smi → rocm-smi → sysfs → nvidia-smi、失敗時 N/A）
  - 単一メトリクス WS ストリーム、1 分平均を SQLite へ保存（保持期間つき）、RAPL による CPU 電力推定
- 電源: reboot / shutdown / systemdユーザーtimerによる予約・取消（Web再起動後も継続、期限切れは再実行しない）

### フロントエンド（React + TS + Vite + Tailwind v4、gzip 約 99KB）
- ログイン / 認証ガード / 401 自動リダイレクト
- デスクトップ: 折りたたみサイドバー、Ctrl+K コマンドパレット（アプリ検索・起動停止・ページ移動・電源）
- モバイル: 下部ナビ 5 項目（中央「操作」→ボトムシート、電源は視覚分離）、Safe Area 対応、FAB
- ダッシュボード: CPU / RAM / GPU / VRAM タイル + スパークライン + 実行中 / 失敗アプリ
- アプリ: カード（主操作 1 個 + ⋯メニュー）、詳細ボトムシート、3 ステップ追加フロー（venv・エントリーポイント自動提案）、削除確認ダイアログ
- ログ: WS リアルタイム追従、仮想スクロール（2 万行保持）、正規表現検索、一時停止、stdout/stderr 切替、DL / 削除
- システム: ホスト / CPU コア別バー / GPU / ディスク / ネットワーク / 上位プロセス
- 設定: テーマ（システム / ライト / ダーク）、セッション管理、監査ログ閲覧（admin）
- 楽観的 UI（起動→即 STARTING）、WS 自動再接続 + 再接続中バッジ、タブ非表示時は購読停止

### スクリプト / 運用
- `scripts/setup.sh`（venv + npm + build + linger）、`run-dev.sh`（起動時 venv 自動構築）、
  `create-admin.sh`、`install-service.sh`（systemd ユーザーサービス、root 不要）

## 検証結果（2026-07-12、Ubuntu 24.04 実機）

- pytest 19 件成功（認証 / CSRF / 権限 / ユニット生成エスケープ / パストラバーサル / symlink 脱出 / マスキング）
- API E2E: 登録→起動→ログ→再起動→停止→ログ削除→監査を curl で確認
- **プロセス継続性**: Web バックエンド kill 後もアプリ継続、再起動後に同一 PID・稼働時間を取得
- WS: 未認証 403 / 偽 Origin 403 / 認証済みメトリクス・ログストリーム受信 OK
- GPU: AMD GPU を amd-smi で取得（使用率 / VRAM 23.3/32GB / 温度 / Hotspot / 電力 / ファン）
- UI: Playwright で 1280 / 390 / 320px を検証。横スクロール 0px、コンソールエラーなし。
  ダッシュボード / アプリ / ログ / システム / 設定 / 操作シート / 追加ドロワー / パレットのスクリーンショット確認
- systemd サービス: `control-deck-web` をユーザーサービスとして登録、非 root（一般ユーザー）で稼働、
  linger 有効化により SSH / ログアウト後も継続

## 既知の制約 / 次の作業

1. system レベルの systemd サービス制御は未対応。helper / polkit の権限境界設計が必要
2. PostgreSQL の運用切替、汎用プラグインSDK、provider共通pull/設定管理は未完（OpenCode向けfeature境界は実装済み）
3. 電源 reboot/shutdown は API 実装済みだが、破壊的な実機実行は未検証

## 履歴

- 2026-07-19: WorkflowとRunnerを「作成・デバッグ」と「公開アプリ」に役割整理。editor主操作を更新して開く／アプリを開く、一覧へ公開版button、URL deep link/reload復元、無効ID復帰表示を追加。`llm.chat`は管理Ollamaをprovider adapterで自動load、llama.cppをsystemd user起動してhealth待機し、外部endpointは非操作。backend全322件、frontend本番build、実サービス、Playwright 3件、未load実機27B modelのload→生成→unload復元を確認
- 2026-07-19: `human.approval`の承認待ちcontractを型付きobjectへ統一し、公開アプリの500を修正。承認文・担当者・期限・承認／却下をeditorと公開アプリへ統合し、API回帰testと320px公開操作E2Eを追加
- 2026-07-19: 実行履歴のNodeRun観測を強化。並列性を読めるGantt風timeline、node/total token、bottleneck、入出力size、実入力・実出力・log・error・artifactの選択式詳細を追加。LLMの`tokens`を`total_tokens`へ正規化し、path/log/artifactを有限長保存。NodeRun出力もSecret値で再redactし、認証tokenと`total_tokens`/`max_tokens`等の数値metadataを区別する共通規則へ修正。backend全319件、frontend本番build、実サービス再起動、320/390/768/1280px Playwright 2件でtimeline・実入力・実出力・横overflowを確認
- 2026-07-19: ワークフロー実行UXを「実行前チェック」「下書きをテスト」「検証して実行」へ整理。前2者は同じ静的preview・公開可否を必ず表示し、draftテストだけがexecutorと副作用を実行する。editor主操作は保存→公開blocking検証→差分時のみversion公開→その固定version実行を1操作化し、変更なしではversionを増やさない。明示的な配備のみは「実行せず公開」へ移動し、Runner等の本番入口は公開版限定を維持。実行入力へaccessibility名も追加。backend全319件、frontend本番build、実サービス再起動、320/390/768/1280pxと公開・draft実行を含むPlaywright 2件成功を確認
- 2026-07-19: Project Lab Web live previewを実装。Web profileの`{host}`/`{port}`自動割当、systemd process treeのLISTEN所有確認、run ID限定同一origin proxy、Cookie/Authorization/CSRF/Set-Cookie遮断、16MiB request上限、sandbox iframe、redirect/絶対path resource、起動待ち・停止を追加。`project_runs.web_port`軽量migrationを追加。backend全318件、frontend本番build、実サービスでpython http.server起動→proxy HTML/CSS→停止後409、320/390/1280px Playwright E2E成功を確認
- 2026-07-19: Project Lab durable runを実装。`ProjectRun`/`ProjectRunArtifact`、CLI/test profileの許可SDK・path検証、配列argvの`systemd-run --user`隔離実行、timeout/resource/concurrency制限、DB状態復元、cancel、1MiB上限・秘密伏せ字log、生成/変更artifact差分とchecksum、監査、operator権限、実行履歴UIを追加。Secret参照は安全なcredential分離まで明示拒否。ランナーをグローバル「操作」シートへ追加し、run権限だけの利用者を編集画面へ送らないよう権限も分離。backend全315件、frontend本番build、実サービスsystemd run→SUCCEEDED/exit 0/artifact/log、Runner 320/390/768/1280px E2E成功
- 2026-07-19: Workflow guided configurationを実装。node metadata v3へ安全な`initial_config`、全設定fieldの推奨値/理由、主要入出力、型付き出力、最短手順、構成例を追加。新規ノードの初期設定、空欄だけへの推奨値適用、接続時の主要入力補完、検索・直前/その他上流・型・直近サンプル付き変数picker、カーソル位置挿入、設定内helpを追加。外部URL/path/model/Secretは自動推測しない。backend全308件、frontend本番build、実サービス再起動、390/320px E2E、横overflow/console errorなしを確認
- 2026-07-19: Application Builder Phase A完了。ApplicationProject、Application Spec v1、Workflow/Application IR、portable type system、structured diagnostics、target/framework/node capability registry、静的validate/CRUD API、ワークフローの「アプリ化」入口と基本Project画面を追加。source生成・build・artifact・自由code LLMは未実装として明示し、dummy成功UIを置かない。node metadata v3へ推奨初期値・理由・help・変数picker hintの互換fieldを追加。backend全307件、frontend本番build、実サービスmigration/health、320/390/768/1280px E2E、横overflowなしを確認
- 2026-07-19: ワークフローの安全プレビューと公開判定を共通preflightへ統一。409の構造化blocking理由を画面表示し、最終出力不足には`output.render`追加を案内する。全サンプルをコピー直後に安全プレビュー・公開前検証・公開できる回帰テストを追加し、既存の監視／復旧／Gitサンプルへ型付き出力を補完。外部サービス不要でfilter・sort・aggregate・並列Table/JSON/Metric出力を扱う「受注データ分析」複合サンプルを追加
- 2026-07-19: AIアシスタントの空の生成状態行を条件描画化。さらにstandalone PWAでだけ有効になるSafe Area paddingとアプリshell下部navigation予約を除去し、入力カードをdialog下端へ密着。実サービスを再起動し、standalone条件の320×700／390×844 screenshot、1280×800、横overflowなしを確認
- 2026-07-17: モバイル下部ナビのリモートデスクトップと操作シートのAIアシスタントを交換
- 2026-07-16: LLM runtimeのcomplete/stream/cancel契約を統合し、永続chatとworkflow生成の重複処理を置換
- 2026-07-16: OpenCodeを既定無効featureとして条件登録し、実機llama.cpp分析、cancel、PC/320pxを検証
- 2026-07-16: ワークフロー並列mapを分離context化し、検索/お気に入りとdata/glob/AI統合nodeを完成
- 2026-07-15: llama.cppを複数GGUF catalog/個別systemd unit化し、共通health/load上限とモデル別idle/自動起動を追加
- 2026-07-15: AIアシスタントを独立route化し、表示名・会話切替/改名/削除と実機ワークフロー生成登録を確認
- 2026-07-15: Model画面をruntime横断化し、llama.cppのK/V・MTP・MoE等の型付きモデル個別設定とAMD custom MCLKを追加
- 2026-07-15: ターミナルをmobile keyboardのvisual viewportへ追従。tmux/xterm 100,000行履歴、再接続snapshot、PC全文コピーを追加
- 2026-07-15: capability付きprovider adapterと共通モデル一覧・ロード・アンロード・削除APIを追加
- 2026-07-15: ClaudeのLLM設定タブ統合を流用し、providerカタログと共通エンドポイント検出APIを追加
- 2026-07-15: アプリ別ヘルスチェック（プロセス/TCP/HTTP status・本文/許可ルート内ファイル）、DEGRADED表示、手動確認UIを追加
- 2026-07-15: 電源予約をWeb内タイマーから永続systemdユーザーtimerへ移行し、予約・取消UIと実行監査を追加
- 2026-07-15: 完了表記を受け入れ条件で再監査。アプリアイコン、ごみ箱、再開可能チャンクアップロードを実装し、古い残件一覧を現状へ更新
- 2026-07-12: リポジトリ初期化。要求仕様原本と初期文書 8 点を記録
- 2026-07-12: PR #1 バックエンド（認証 / RBAC / 監査 / アプリ管理 / systemd / 監視 / 電源 / スクリプト）
- 2026-07-12: PR #2 フロントエンド（レイアウト / ダッシュボード / アプリ / ログ / システム / 設定）+ amd-smi パーサー修正
- 2026-07-13: リモートデスクトップ描画の根本修正（WS トンネルの Guacamole 命令境界保存）+ タッチ操作をタッチパッド方式に刷新（長押しドラッグ / 2本指右クリック / 3本指キーボード）+ タッチ端末は2倍解像度で接続し縮小表示
- 2026-07-13: ターミナル永続化の根本修正（tmux を systemd-run --user --scope で独立 cgroup 起動。サービス再起動で tmux ごと kill されていた）+ WS 自動再接続 + モバイル向けコピー/貼り付けシート
- 2026-07-14: 最新RAG/Deep Search強化: 外部検索ノードを4ソース統合(arXiv/Crossref/PatentsView特許[要無料キー]/SEC EDGAR市場調査)、Web検索ノード新設(DuckDuckGo/SearXNG・URL復元)、RAG検索にHyDE+マルチクエリ(RAG-Fusion)追加、Deep Researchノード(サブ質問分解→多ソース反復探索→引用付きレポート)。Deep Searchはノード組合せ(Web検索→スクレイピング→RAG構築→rag.query(HyDE)→LLM統合)でも構築可能
- 2026-07-14: Model(Ollama)管理タブ追加（一覧/取得[Ollamaレジストリ+HuggingFace GGUF検索]/削除/ロード/アンロード/詳細/keep_alive・アイドル自動アンロード[expires_at変化で活動検知]・呼び出し時オートロード・既定モデル設定・pull進捗WS）+ GraphRAG（LLMでトリプル抽出しグラフ化、graph検索モード、Knowledgeにグラフタブ）
- 2026-07-14: Knowledge(RAG) 超強化: RAG エンジン v2（コレクション設定/文書管理/6チャンク戦略[recursive/fixed/sentence/paragraph/markdown/parent_child]/SQLite FTS5 trigramで日本語全文/ベクトル・全文・ハイブリッド(RRF)検索/親子チャンク）+ Knowledge タブと管理ページ（コレクションCRUD・文書取り込み[テキスト/URL/ファイル]・検索テスト・設定）+ ノード統合強化（rag.build/rag.query に戦略・検索方式を選択追加、学術検索ノード[arXiv/Crossref]追加）。ノードは乱立させず統合方針
- 2026-07-13: Web スクレイピング強化: 抽出ビューワ（サニタイズ HTML をサンドボックス iframe に描画→要素クリックで CSS セレクタ自動生成、候補セレクタ一覧、抽出ワード↔結果の対比プレビュー）、複数抽出項目（各項目が出力変数、属性 text/html/href/src 等・複数取得選択可）、単一 selector との後方互換 + 下部ナビを fixed からフロー内配置に変更（iOS Safari 下部ツールバーによる浮き上がりバグ修正）
- 2026-07-13: ワークフロー v3（Dify/n8n 流）: トリガーに型付き入力フィールド定義（テキスト/長文/数値/選択/ファイル、実行時入力ダイアログ）、全後段から参照できる変数ピッカー（ノード出力メタデータ + 名前付き変数 {{vars.*}}）、LLM の稼働サーバー検出 + 構造化出力（json_object / json_schema + プリセット、非対応サーバーへはプロンプト埋め込みフォールバック）、全ノードに出力変数名設定、新ノード util.now / http.download
- 2026-07-13: GitHub 管理（リポジトリ登録でクローン/更新/保存/リバート/削除をボタン操作、~/ControlDeckApps へ格納、gh auth login のターミナル連携）+ 下部ナビ再編 + Overlay フォーカス奪取バグ修正
- 2026-07-13: アプリに Web ボタン（プロセスツリーの LISTEN ポートを検出しブラウザで開く。複数ポートは初回選択→ web_port として保存、設定編集で検出ポートから変更可）
- 2026-07-13: アプリ機能の使い勝手改善: テスト実行のストリーミング化（WS /apps/test-run/stream、常駐アプリ対応・停止ボタン）、実行 cwd を既定ホームに（test-run とユニットの WorkingDirectory）、パス入力にサーバー側ファイル選択ダイアログ（FilePicker）、リモートビューアのタッチ判定を pointer:coarse に精緻化
