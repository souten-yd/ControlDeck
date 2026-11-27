# 通知の種類とお休みモード（2026-11-23）

すべての通知は `backend/app/alerts/events.py::notify_event(kind, ...)` を通る。判断の順: ① 利用者ごとの種類の有効・無効 → ② お休みモード → ③ `webpush`。設定は `data_dir/push/notify-prefs.json`（利用者 ID ごと・上書きした値だけ）。画面は 設定 → 通知。API は `GET/PUT /api/v1/notification-settings`（本人の分だけ）。

## お休みモード
既定は有効・22:00〜07:00（サーバーの地域の時刻、日またぎ可。開始を含み終了を含まない）。休み中の通知は送らず捨てる。緊急の監視の警報（app_down・app_health_failed・gpu_temp_c・cpu_temp_c）は「お休み中も送る」を有効にしたときだけ通す（既定は無効。2026-11-23 利用者「お休み中の通知は届かないようにしてほしい」）。

## 種類
| 種類 | いつ | 既定 | 検出の場所 |
|---|---|---|---|
| code_done | OpenCode の回の完了 | 有効 | `workflows/chat_persist.py` の chat.completion ジョブの終わり → `alerts/chat_events.announce_turn` |
| code_question | 質問待ち・プランの「決めてもらいたいこと」 | 有効 | `on_tool`（question が running）、`announce_turn`（plan の答え） |
| code_permission | 許可待ち | 有効 | `on_permission`（pending） |
| code_failed | OpenCode の回の失敗 | 有効 | `announce_turn(failed=True)` |
| code_interrupted | 再起動で中断 | 有効 | `jobs/service.py::recover_on_startup`（#561） |
| research_done | 調査（web・academic・deep・research）の完了・失敗 | 有効 | `announce_turn` |
| chat_done | 60 秒以上かかった会話の答え | 無効 | `announce_turn` |
| workspace_gate | 作業場の関門の合格・不合格 | 有効 | `integrations/opencode/router.py` の関門ジョブ |
| workspace_adopted | 取り込み・マージ | 有効 | 同 adopt・merge |
| workspace_disk | 空きが少なくて保存できない | 有効（1 時間に 1 回） | `chat_persist.autosave_turn` |
| team_task | Nexarch の AI チームのタスクの報告・停止 | 有効 | `commander/team_watch.py::tick`（60 秒ごとの見張り） |
| project_run | Project Lab の実行の終わり | 有効 | `project_lab/runs.py::_announce_finished` |
| media_done | 画像・音楽・3D などの生成・編集の完了・失敗 | 有効 | `addons/execution.py::create_agent_tool_job` |
| model_error | モデルの起動の失敗（管理者・10 分に 1 回） | 有効 | `models_mgmt/gateway.py` |
| update | 更新・承認待ち | 有効 | `maintenance/auto_update.notify`（通知チャンネル push 経由） |
| alert | 監視の警報 | 有効 | `alerts/engine.py`（通知チャンネル push 経由） |
| patent_watch | 特許の期限（件数だけ） | 有効 | `patents/watch.py`（通知チャンネル push 経由） |

update・alert・patent_watch は従来どおり、通知チャンネル「この ControlDeck の通知」（push）を選んだときだけ届く。

## 載せない物
会話の本文の全文・パス・鍵・URL は載せない（題は会話の題、本文は 1 行の要約。`one_line` が伏せる）。秘密の会話は題も要約も載せず「秘密の会話」とだけ出す（Push は Apple・Google の push service を通る）。特許の画面（Patent）の AI 作業は会話の通知を出さない。

## 入れなかった案
- 特定の「特許の会話」だけを見分ける印は会話に無いため、秘密の会話の判定（`confidential_at`）に任せた。
