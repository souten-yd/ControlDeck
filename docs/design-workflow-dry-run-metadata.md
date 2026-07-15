# ワークフロー dry-run・ノードmetadata 詳細設計

最終更新: 2026-07-15

## 背景と再監査結果

既存の構造検証・意味検証・品質スコアは定義不備を検出できる。一方、エディタの
「このノードをテスト実行」は通常executorを直接呼ぶため、`file.write`、`app.stop`、
`notify.webhook`等では実際の副作用が発生する。これは副作用なしdry-runではない。
またLLM生成用catalog、backend executor、frontend node定義が別々で、実装済みノードの
欠落や能力表示の不一致を機械的に検査できない。

## 不変条件

1. dry-runは`NODE_EXECUTORS`を一切呼ばず、プロセス、ネットワーク、LLM、DB更新、
   ファイル書込、アプリ操作、secret復号を行わない。
2. dry-runは`WorkflowExecution`を作成せず、通常実行・スケジューラーの状態を変えない。
3. secret値を応答・ログへ含めない。config内の機微キーと`{{secrets.NAME}}`の名前をredactする。
4. 構造エラーと意味エラーは`valid=false`、改善警告は`warnings`、実行時に行うはずの
   操作は`plan`として返し、「成功した」とは表現しない。
5. 実行可能性と副作用分類はbackendのmetadataを正とし、frontendはAPIから能力を補強表示する。

## Node metadata契約

全executorと`control.loop`に次を持たせる。

- `type`, `version`, `description`
- `side_effect`: `none` / `read` / `write` / `external` / `process`
- `capabilities`: `network`, `llm`, `filesystem.read`, `filesystem.write`, `apps.control`,
  `process.exec`, `database`, `notification`, `workflow.call`等
- `config_schema`: 主要設定キーの型と必須性
- `output_schema`: 代表的な出力キーと型
- `supports`: retry / cancel / progress / dry_run

`GET /api/v1/workflows/node-catalog`で返し、executorとmetadataの集合差をテストで禁止する。
LLM生成promptも同じcatalogを使い、未掲載の実装済みノードをなくす。

## dry-runアルゴリズム

1. `engine.validate_definition`でID、trigger、edge、未知typeを検査する。
2. `semantic_check`で未接続、必須設定、参照不整合を検査する。
3. triggerからedgeを幅優先に辿り、循環は訪問済みとして有限化する。条件分岐は両枝を
   「条件次第」、loop bodyは最大反復数を表示するだけで展開・実行しない。
4. 各ノードを`SIMULATED`として、順序、依存元、副作用、必要capability、redact済みconfig、
   実行時に行う操作の要約を返す。実行結果の捏造はしない。
5. 副作用ノード数と分類、到達不能ノード、承認・retry設定をsummaryへ集約する。

APIは保存済み用`POST /workflows/{id}/dry-run`と、編集中定義用
`POST /workflows/dry-run-definition`を提供する。いずれも`workflows.run`権限を要求する。

## UI/UX

- 通常の「実行」は主操作のまま維持し、「安全ドライラン」はその他メニューへ置く。
- 結果sheetでvalid、エラー/警告、副作用分類、予定順を表示する。dry-runが外部操作を
  実行していないことを明記する。
- ノード設定の既定ボタンは「安全プレビュー」とし、従来の実executor呼出を既定にしない。
- ノードには副作用badgeと必要capabilityを表示し、特にwrite/process/externalを識別できるようにする。

## 受入条件

- monkeypatchした全executorが呼ばれないこと、DB execution件数・対象ファイル・外部mockが
  変化しないことを自動テストする。
- executor、backend metadata、frontend node typeの集合整合をテストする。
- 構造/意味エラー、循環、分岐、loop、secret redactionをテストする。
- backend全テスト、本番build、実サービスAPI、1280px/320pxの結果sheetを確認する。
