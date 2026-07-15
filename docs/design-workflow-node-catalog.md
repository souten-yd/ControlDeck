# ワークフローノード拡張・パレット 詳細設計

## 再監査結果と統合方針

既存executor、engine、backend metadata、LLM catalog、frontend catalogを集合比較した。既存の
`control.loop`には`parallel`実装があるが、反復間で同じcontextを共有して`item/index`とbody出力を上書きするため、
並列mapとしては正しくない。HTTP health checkは`http.request`の`expect_status`で既に満たし、単独nodeを増やすと
重複する。JSON/CSV/schema、glob、embedding/rerank/judgeは同等機能がない。

追加は用途ごとにnodeを細分化せず、設定`operation`で切り替える統合nodeを優先する。

| 計画上の要望 | 実装境界 |
|---|---|
| parallel map | `control.loop`の`parallel=1..5`を修正し、反復ごとの分離contextと順序付き`results`を返す |
| JSON transform / schema validate / CSV | `data.transform`のoperationとして統合 |
| glob | 許可root検証を各結果へ強制する`file.glob` |
| health check | 既存`http.request` + `expect_status`を利用し、UI説明とプリセットを補強 |
| embedding / rerank / judge | `ai.utility`のoperationとして統合。OpenAI互換embedding、一般的なrerank、LLM judgeを扱う |

## 実行・データ契約

### control.loop

- `parallel`は1〜5、itemsは最大100を維持する。
- 各iterationは開始時点の親contextを浅いsnapshotにし、loop自身の`item/index/total`と`__vars__`を分離する。
  bodyのexecutorとtemplate展開は必ずiteration contextを参照する。
- 完了後は入力順の`results=[{index,item,outputs}]`をloop出力へ格納し、互換の`index/item/total/done`も維持する。
  親contextへは最後のiterationのbody状態だけをmirrorし、既存のdone側参照を壊さない。
- 1 iterationの失敗でTaskGroupを取消し、workflowの既存cancel/timeoutを継承する。

### data.transform

- `json_parse`: template展開したtextをJSONへ変換。
- `json_get`: objectとdot path（array index対応）から値を抽出。
- `json_set`: objectをdeep copyしdot pathへJSON valueを設定。prototype等の概念はなくdict/listだけを扱う。
- `schema_validate`: Draft 2020-12 JSON Schemaで検証し、`valid/errors/value`を返す。schema自体の不正も拒否する。
- `csv_to_json` / `json_to_csv`: Python標準`csv`を使い、入力最大2MB、行最大10000、出力最大2MB。

### file.glob

- base directoryを既存`files.resolve()`でrealpath/allowed-root検証する。patternは相対globだけとし、絶対pathと`..`を拒否。
- symlinkを含む各matchも`resolve()`へ戻して検証し、base外へ出る結果を除外する。最大1000件、安定sort。
- `files/directories/all`、recursive、最大件数を選べ、path/relative_path/name/size/is_dirを返す。

### ai.utility

- endpoint/model/API key、inputをserverへ永続化せず、その実行だけで利用する。エラー本文やkeyはユーザー/ログへ返さない。
- `embedding`: `<base_url>/embeddings`へOpenAI互換requestし、vectors/dim/countを返す。
- `rerank`: `<base_url>/rerank`へquery/documents/model/top_nを渡し、score/index/documentを正規化する。
- `judge`: `<base_url>/chat/completions`へ評価対象、rubric、0〜100 schemaを渡し、score/reasonを返す。
- 入力件数100、各文書32KiB、HTTP timeout 5〜300秒、応答2MiBを上限とする。LLM起動前GPU profileも既存preflightで適用する。

## パレットUI

- backend `node-catalog`を表示可能nodeの正とし、取得完了までは全nodeを仮表示しない。
- 検索はtype、表示名、説明、categoryを大文字小文字を無視して絞る。
- お気に入りはユーザー端末のlocalStorageへtype IDだけ保存し、先頭の「お気に入り」群へ表示する。
- 「利用可能のみ」は常時既定ON。OFFはfrontend catalogにあるがbackendにないoptional nodeも「利用不可」として確認でき、
  追加ボタンは無効にする。これにより導入済み機能のみ表示と不足featureの発見性を両立する。
- 320pxでは検索、filter、お気に入り操作を1列/折返しにし、node card内の主操作は追加、星は独立aria-labelを持つ。

## metadata・検証

executor追加時にbackend catalog、required keys、side effect、capability、config/output schema、frontend定義を同時更新する。
集合差の既存テストに加え、各operationの境界値、glob脱出、parallel item分離、optional node絞り込みを回帰テスト化する。
dry-runはexecutorを呼ばず、追加nodeも宣言metadataだけを返す。
