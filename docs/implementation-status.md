# 実装状況

最終更新: 2026-07-19

## 公開ワークフロー・ランナー（2026-07-19）

- 公開済みWorkflowをキャンバスなしで操作する「ワークフロー・ランナー」を`/runner`へ追加。公開版の入力form、
  想定output contract、副作用区分、実行、停止、human approval、typed output、最近の実行、過去入力再利用を同じ画面へ統合した。
- 専用`/workflow-runner` APIは公開名・説明・version・input/output schema・結果だけを返し、definition、node/edge/config、
  runtime snapshot、source node IDを返さない。draft/test executionもRunnerから参照できない。従来のdefinition／node debug APIは
  `workflows.edit`へ制限し、`workflows.run`だけのoperatorは公開Runner APIを利用する境界へ修正した。
- 公開時にtrigger inputsと`output.render`からJSON Schemaを生成してimmutable `WorkflowVersion`へ保存。versioned description列を
  SQLite light migrationへ追加し、既存公開版の空contractは起動時に安全なsnapshotからbackfillする。draftの定義・説明変更は再公開まで
  Runnerへ反映されない。
- editor PreviewとRunnerで13入力型とtyped rendererを共有する`RuntimeComponents`を追加。iPhone下部navigationのWorkflowをRunnerへ置換し、
  editorはedit権限の利用者だけに表示する。AI assistantの公開workflow実行もRunner APIへ移行した。
- Project LabとApplication Builderの新規要件を監査し、`docs/design-workflow-runner-project-lab.md`と
  `docs/design-application-builder.md`へ共通IR、決定的generator、Phase A限定初回PR、反復GUI editor、structured AI patch、
  design system、platform advisor、Web/Avalonia/Tauri優先、build/artifact境界を記録した。

検証: backend全298件、frontend production build成功。実ControlDeck serviceを再起動し、実DB migrationとhealthを確認。
認証付きPlaywrightで320×700から公開workflowを作成・公開し、Runnerでparagraph入力→Markdown output、過去入力再利用、
キャンバス／内部node名非表示を確認。320×700、390×844、768×1024、1280×800でdocument/body横overflow 0、console error 0。
backend testではrun-only operatorがRunnerを利用でき、definition/list/debug execution APIは403になることを確認した。

## Workflow Phase 3 human approval／control merge（2026-07-19）

- 隠し共通設定だった承認gateを正式な`human.approval` nodeへ昇格。上流変数を使う承認文、ユーザー名による
  承認者限定、0.1秒〜24時間の期限、承認／却下の監査、承認後のtyped outputをcatalog・metadata・UIへ統合した。
- 承認待ち情報を実行パネルに表示。resolved secretは表示・node output前にredactし、却下は
  `APPROVAL_REJECTED`、期限切れは`APPROVAL_TIMEOUT` Error Contextとしてerror／timeout routeへ渡す。
- `control.merge`を追加し、wait_all、first_success、first_complete、quorum、collectをengineの到着順・成功状態と統合。
  直接上流だけを`items[{node_id,status,output}]`、`values`、`value`へまとめ、成功0件／quorum未達は明示errorにする。
- semantic checkに承認期限、merge方式、入力本数、quorum範囲を追加。node referenceとREADMEを標準45 nodeへ更新した。
- 現段階の承認待ちは既存engineと同じprocess memory上にあり、service再起動をまたぐ永続pause tokenと修正入力は
  `WorkflowPause` migrationを伴う次の機能単位で実装する。

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
| Phase 2 — アプリ管理 | ✅ コア完了（アイコン・TCP/HTTP/ファイル等のヘルスチェック対応済み） |
| Phase 3 — 監視 | ✅ コア完了（アラート通知を含む。アプリ別GPU等は残り） |
| Phase 4 — ファイル + ターミナル | ✅ コア完了（ごみ箱・再開可能アップロード対応済み） |
| Phase 5 — ワークフロー | ✅ コア完了（下記参照） |
| Phase 6 — リモートデスクトップ | ✅ コア完了（guacd トンネル + 接続管理 + ビューア） |
| Phase 7 — TOTP ほか | ✅ コア完了（TOTP/PWA/バックアップ。WoL はワークフローノードで対応） |
| Phase 5b — ワークフロー統合開発環境 | 🚧 Phase 1完了、Phase 2はsnapshot/retry/node run/pin/部分再実行/回帰テストまで実装 |

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
2. アプリ別 GPU 使用量と、許可コマンド型ヘルスチェック（コマンド許可リスト基盤）は未実装
3. ファイルの圧縮・解凍と PDF / 音声 / 動画等の高度プレビューは未実装
4. PostgreSQL の運用切替、汎用プラグインSDK、provider共通pull/設定管理は未完（OpenCode向けfeature境界は実装済み）
5. 電源 reboot/shutdown は API 実装済みだが、破壊的な実機実行は未検証

## 履歴

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
