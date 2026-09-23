# Add-on agent失敗時のJob追跡

Status: source HTTP/stdio/永続化と全体test受入。installed/実OpenCodeはNOT TESTED。
Branch: fix/addon-failure-job-context。基準ea72128。

商店街の実MCP要求が処理開始後に失敗すると、受理済みJob IDが失われ、呼ぶ側が
失敗Jobを確認できなかった。MediaForge側がIDを返すだけではHostのエラー変換で
破棄されるため、汎用Hostの別PRとする。設計はdesign-addon-platform-v2へ先に追記。

## 変更

- upstreamの短いcodeに加え、任意のopaque Job IDと既知状態だけを受理する。
  Host job_idとupstream_job_idを混同しない。パス、長すぎるID、任意本文は通さない。
- 失敗したHost Jobのresultへerror/code/参照を保存し、failed/canceledを維持。
  同期待機のエラーにもHost IDを付ける。未受理ならIDを作らない。
- HTTP→stdio MCPでisError=trueのまま構造化参照と短い説明を返す。
  説明が長くても参照は削らない。成功応答/owner/RBAC/自動再送方針は不変。
- Media固有のAPI/ID接頭辞/モデル/依存は追加しない。

## 実行証拠

最終`./deck.sh test`: 1150 passed、2 skipped、1warning、105.75秒。exit0。
関連71testsは4.08秒で通過。最後にJobsを含む82testsも5.16秒で通過。
途中の全体gateは、sensor呼出を先頭に拾う既存ProjectRun testと、固定1秒待機の
既存排他Job testが各1回失敗。前者は単独通過、後者は単独10/10通過を確認し、
製品処理/待機期限/判定条件を変えずに最終全体gateを通した。
新テストの初回は誤ったORM型名で失敗、次は
標準fixtureで無効なOpenCode機能gateのため405。型名を現行Jobへ合わせ、
既存MCP試験同様にproduction routerを隔離appへ載せて修正した。製品のgateは緩めていない。

private evidenceはMediaForge managed dataの
`maintenance/shopping-street-20260923/failure-context-source`。
`probe.py`を実行し、独立UvicornのHost router、制御したAdd-on HTTP、実stdio bridgeを接続。
Host DB/user/keyは隔離した試験用で、稼働Hostの認証・データを読み書きしていない。

- terminal failure: isError=true、Host IDと上流ID、failedを保持。
- upstream wait timeout: isError=true、両IDを保持。未知の終端を付けない。
- 不正pathをIDとする上流応答: 上流参照/状態を除外し、内部パスを出さない。
- success: 既存の成功結果を維持。
- HTTP実呼出4回、Host DBはfailed3/succeeded1、全て同一owner。再送0。
- 別の実MediaForge Uvicorn（Host transportのみ明示fake）でもunsupported packが
  502/code/同一Job ID/failedを返し、保存Jobと一致。

report.jsonと各process logを保存。所有3サービスは試験後に終了、fixture tokenは削除。
モデル取得/推論0。実稼働更新・実OpenCode/新error表示の受入は次段。
