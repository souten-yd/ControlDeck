# ワークフロー・ランナー / Project Lab 詳細実装仕様

最終更新: 2026-07-19

実装状況: Runner完了。Project Lab core discovery／manifest／read-only artifact browser完了。durable run以降は未着手。

## 1. 目的と境界

公開済みワークフローを、編集キャンバスやノード定義を見せずに入力・承認・結果確認だけで運用できる「ワークフロー・ランナー」を追加する。あわせて `~/CodeDEV` の開発成果物を自動検出し、CLI、Web、HTML、画像・グラフ、構造化データ、ログ、エラーを同じ画面で実行・評価する「Project Lab」を段階導入する。

ランナーは編集画面の表示切替ではなく、公開版だけを読む専用APIをセキュリティ境界とする。Project LabはOpenCode optional featureに依存しないコア機能とし、OpenCodeは必要な場合だけAI修正・評価を行う追加能力として統合する。

Project Labは場当たり的な実行器にはせず、Application Builderのbuild/live preview/artifact評価面を兼ねる。共通IR、決定的generator、target capability、build isolationは `docs/design-application-builder.md` を正とする。

## 2. コード監査

| 機能 | 現在の実装 | 実装場所 | 問題 | 方針 |
|---|---|---|---|---|
| 公開版固定 | `WorkflowVersion` と `published_at`、通常runは公開版を選択 | `backend/app/workflows/engine.py`, `router.py` | versionのinput/output schemaが空のまま | 公開時に契約を生成・固定して改修 |
| 実行一覧・詳細 | 入力、出力、context、definition/runtime snapshotを返す | `GET /workflow-executions*` | `workflows.run`だけで内部定義を取得可能 | 内部定義APIはedit権限へ締め、run権限にはランナー専用最小APIを追加 |
| 入力フォーム | trigger `config.inputs` から13型を描画 | `PreviewWorkspace.tsx`, `nodeTypes.ts` | draftテスト用UIに閉じている | 共通ランタイム部品へ抽出して再利用 |
| 型付き出力 | `signal.display` / `output.render` をoutput contract化 | `_final_outputs`, `PreviewWorkspace.tsx` | source node IDを含み、公開前の想定schemaが固定されない | ランナー応答から内部IDを除去し公開schemaを表示 |
| 承認 | `human.approval` とメモリ待機、approve API | workflow engine/router | 編集詳細APIを見ないと操作しにくい | ランナー用承認操作と最小pending情報を追加。永続pauseは別Phase |
| CodeDEV検出 | `~/CodeDEV` を列挙・import | `integrations/opencode/provider.py` | OpenCode有効時にしか使えず、成果物評価と混在 | generic discoveryをProject Labへ切り出しOpenCodeから再利用 |
| コード実行 | インラインPython/shellのWebSocketテスト | `applications/testrun.py` | 一時コード用、切断で終了、プロジェクト成果物・履歴なし | Project Runモデルとsystemd user transient unitへ置換 |
| Web表示 | managed appのlocalhost portを同一origin proxy | `applications/webview.py` | ManagedApplicationに限定 | run IDに紐づくlocalhostだけをproxyしcookieを除去 |
| 成果物表示 | workflow typed outputとapp iframeが個別 | workflow preview / Apps | HTML・CSV・画像・ログの統合評価面がない | artifact catalogとrendererを共有 |
| プロセス分離 | OpenCodeは`systemd-run --user` | `integrations/opencode/provider.py` | Project Lab向けprofile、制限、永続状態がない | 配列argv、固定cwd、timeout/resource limit、audit付きで再利用 |

READMEの記述だけでなく、上記は実コード・API・UI導線・テストの存在を確認した結果である。

## 3. ワークフロー・ランナー

### 3.1 公開アプリ契約

公開時にtrigger inputからJSON Schemaを生成し、元の表示順・placeholder・sample等は `x-control-deck-fields` に保持する。`output.render`、`signal.display`、`flow.return`からoutput schemaを生成する。テンプレート値、node ID、接続、設定値、secret値は契約に含めない。

公開契約はimmutable `WorkflowVersion`へ保存する。draftを保存してもランナー表示と実行は変化せず、再公開時だけ切り替わる。

### 3.2 専用API

- `GET /workflow-runner`: 公開済みアプリのID、公開名、説明、version、公開日時、input/output概要、副作用区分だけを返す。
- `GET /workflow-runner/{id}`: 公開版の入力・出力契約を返す。definition、node、edge、config、runtime snapshotは返さない。
- `POST /workflow-runner/{id}/runs`: 公開版だけを入力検証後に実行し監査記録する。
- `GET /workflow-runner/{id}/runs`: 当該ワークフローの最近の実行を返す。
- `GET /workflow-runner/executions/{id}`: 状態、時刻、redact済み入力、内部source IDなしの型付き最終出力、承認待ちだけを返す。
- `POST /workflow-runner/executions/{id}/cancel`: 実行を停止する。
- `POST /workflow-runner/executions/{id}/approval`: 公開された承認メッセージに対する承認・却下。

全APIは `workflows.run` を必須とする。既存の編集・デバッグAPIは後方互換のため残すが、ランナーUIは一切呼ばない。

### 3.3 UI

ページ名は「ランナー」、routeは `/runner`。左に公開アプリ一覧、右に入力・実行・結果を置き、390px以下では一覧→実行面の1カラム遷移とする。キャンバス、ノード名、実行経路は表示しない。

実行面には公開version、入力フォーム、想定出力、副作用、実行ボタン、最近の実行、typed output、失敗概要、承認/却下、cancelをまとめる。入力値は実行後も保持し、過去実行の入力読込を可能にする。主要操作は2ステップ以内、touch targetは44px、Safe Areaを適用する。

## 4. Project Lab

### 4.1 自動検出

固定root `~/CodeDEV` を `Path.expanduser().resolve()` し、その直下のプロジェクトを列挙する。symlinkを追跡した結果がroot外なら拒否する。次を読み取り専用で検出する。

- Python: `pyproject.toml`, `requirements*.txt`, `*.py`, FastAPI/Flask/Streamlit/Gradio候補
- Node/Web: `package.json`, Vite/Next等のscripts、静的 `index.html`
- 成果物: HTML、PNG/JPEG/WebP/SVG、CSV/TSV、JSON、Markdown、PDF、audio/video、log/text
- Git: branch、dirty、最終更新（秘密ファイル内容は読まない）

自動検出は実行候補を提示するだけで、プロジェクトを自動実行しない。

### 4.2 明示的manifest

任意の `.controldeck/project.json` に表示名、説明、profileを定義できる。profileは `cli`、`web`、`static_html`、`test`、`artifact`。commandは文字列でなくargv配列、cwdはproject内相対パス、environmentは許可された非秘密値のみ、secretは名前参照、artifactはproject内globとする。

### 4.3 実行と隔離

実行はWebサーバーの子プロセスとして保持せず、`systemd-run --user` transient serviceで起動する。`shell=True`は禁止。解決済み実行ファイル、引数配列、project内cwdを検証し、時間、出力、ファイルサイズ、CPU/メモリ、同時実行数を制限する。起動・停止・失敗はauditへ記録し、stdout/stderrのsecret候補をredactする。

Web profileはControlDeckが割り当てたlocalhost portだけをrun ID経由でproxyし、ControlDeck session cookieを転送しない。static HTMLは認証済み配信、path containment、CSPとsandbox iframeを使う。ネイティブGUIを直接埋め込まず、Matplotlib等はheadless backendで画像/HTML artifactへ保存する。ネイティブGUIが必須な成果物は既存リモートデスクトップへの明示導線とする。

### 4.4 評価画面

Project Labはプロジェクト概要、実行profile、console、Web preview、artifact、エラーのタブを持つ。HTML/Webを大きく表示し、画像・グラフgallery、CSV table、JSON tree、Markdown、PDF、downloadを型別表示する。実行終了コード、経過時間、エラー位置、生成/変更artifactを同じrun snapshotへ保存する。

LLM評価は任意操作とし、ソース、ログ、artifact metadata、テスト結果をredact・サイズ制限してローカルLLMへ渡す。結果は評価根拠、問題、改善案、関連ファイル、再現手順を返す。OpenCode利用可能時だけ差分提案・適用前previewを追加する。

### 4.5 Core API（実装済み）

- `GET /project-lab/projects`: CodeDEV直下のproject summary。本文previewは生成しない。
- `GET /project-lab/projects/{id}`: manifest、technology、Git、artifact metadata、実装済みcapabilityを返す。
- `GET /project-lab/projects/{id}/previews/{path}`: 選択されたJSON/CSV/text系だけを遅延解析し、redact済みpreviewを返す。
- `GET /project-lab/projects/{id}/artifacts/{path}`: 認証済みraw artifactをinline配信し、`download=true`で保存する。

全APIは`project_lab.view`を要求する。summary/detailで全artifact本文をReact stateやAPI payloadへ載せず、選択時だけ256KiB以下のtext、JSON、最大200行の表を解析する。binaryと巨大fileはstreaming responseまたはdownloadを使う。

## 5. 実装順

1. 公開schema固定、専用Runner API/UI、入力・出力・承認・履歴、PC/390/320 E2E。
2. Project Lab core discovery、manifest schema、read-only artifact browser。**完了**
3. durable ProjectRun/ProjectArtifact migration、systemd CLI/test、stream/cancel/error。
4. Web proxy/static HTML/graph renderer、artifact差分。
5. LLM評価、OpenCode patch preview、回帰評価。

各段階を独立PRとし、既存workflow/app/OpenCode APIを壊さない。

## 6. 完了判定

- run-only権限の通信応答にworkflow definition、node、edge、config、runtime/secret値が含まれない。
- draft変更は再公開までRunnerへ反映されず、実行も公開版を使う。
- 320pxで公開アプリ選択→入力→実行→typed output/承認が操作できる。
- CodeDEV外path、symlink escape、文字列shell command、任意proxy portを拒否する。
- CLI/Web/static/artifact/errorの各E2E、backend全test、frontend build、実サービスで確認する。
- `docs/implementation-status.md`、README、API/運用説明を更新する。
