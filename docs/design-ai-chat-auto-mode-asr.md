# AIチャット UI・自動モード・長文ストリーム・ASR 詳細設計

更新日: 2026-07-17

## 1. 目的と確定要件

AIアシスタントを他のControl Deck機能と同じUI原則へ揃え、入力内容から処理モードを自動選択する。
長時間処理を含む実行前確認は、利用者の追補指示により行わない。明示的なモード上書きと処理状態・停止導線は残す。

- 右上の閉じる操作は44px以上のタッチ領域、ラベル、強いコントラスト、focus ringを持つ。
- 全体を共通のzinc/accent、控えめな境界・影、PC中央カラム、モバイルSafe Areaへ統一する。
- 通常は「自動」。入力を `chat / web / academic / deep / research / gen / run` に分類し、送信前に選択結果を表示する。
- 利用者はモードメニューで自動判定を上書きできる。ワークフロー編集権限のない利用者へ生成を割り当てない。
- 音声ボタン初回押下時だけローカルASRを導入し、以後は同じ実行環境・モデルを再利用する。
- 発話後1.2秒の無音で確定し、文字起こし結果をそのまま送信する。LLM応答中はマイクを開始できない。
- 音声停止ボタンは録音を即時終了する。発話済みなら認識し、未発話なら破棄する。

## 2. 自動モード判定

判定は応答開始を遅らせず、説明可能かつテスト可能な決定規則をクライアントで実行する。
優先順位は副作用を伴う明確な意図から `run → gen → deep → academic → web → chat` とする。

| モード | 主な判定意図 | 実行 |
| --- | --- | --- |
| run | 「ワークフローを実行/走らせる」と登録済み名称の一致 | 対象を自動選択して実行。一意に決まらなければ対象選択を表示 |
| gen | 「ワークフロー/フロー」を「作成/構築/生成」 | サーバージョブで生成・検証・登録・動作確認・修正を開始 |
| deep | 詳細調査、Deep Research、複数ソースのレポート | Deepサーチ |
| research | Webと学術情報の組み合わせ、結果の不足評価と反復 | 複合調査 |
| academic | 論文、arXiv、学術、先行研究、引用検索 | 学術検索 |
| web | 最新/現在の情報、Web検索、ニュース、価格、天気 | Web検索 |
| chat | 上記以外 | 通常対話 |

入力欄の上に判定結果と短い理由を表示する。明示モードは会話中だけ維持し、「自動へ戻す」で解除する。
自動生成は確認なしで開始するが、生成ジョブは既存の所有者分離、監査、キャンセル、永続化契約を使う。

### 2.1 ハイブリッド判定と複合調査

明確な依頼を遅延なく扱う決定論ルールと、曖昧・複合的な依頼を扱うLLMプランナーの二段構成とする。

- ワークフローの作成・実行、明示的なWeb検索、学術検索、Deep Researchはルールで即時判定する。
- 挨拶など明らかな通常対話もルールで即時判定する。
- それ以外は選択中のローカルLLMへJSON Schema付きで問い合わせ、`chat / web / academic / research`
  と検索手順を構造化出力させる。temperatureは0、thinkingは無効、出力上限は768 tokenとする。
- 構造化出力非対応モデルでも本文からJSON objectを1個だけ抽出し、Pydanticで再検証する。
  失敗時は従来ルールへフォールバックし、送信そのものは失敗させない。
- 構造化出力はOllama固有機能にしない。provider共通層でOpenAI標準`json_schema`、`json_object`、
  prompt制約のみの順にdialectを切り替える。llama.cppはVulkan/ROCmとも同じOpenAI互換経路を使い、
  Ollamaだけnative `format`へ最適化する。400/404/415/422/501だけを非対応判定とし、401/429/5xxは再送しない。
  同じ候補生成をAIアシスタント、ワークフロー生成、LLMノード、GraphRAG抽出で共有する。
- `research` はWeb・学術検索を組み合わせる。初期計画の各手順を実行後、LLMが情報不足を評価し、
  必要な場合だけ追加クエリを生成する。標準3回、絶対上限5回、検索呼び出し合計8回とする。
- URLで出典を重複排除し、最終生成では収集した根拠に通し番号を付けて引用付きで要約する。
- 判定理由、計画、各検索、再評価の進捗はジョブイベントとメッセージmetaへ保存し、再接続後も復元する。
- 通常回答とワークフロー生成の共通出力上限は新規環境で8,192 tokenを初期値とし、Model設定から
  131,072 tokenまで変更可能とする。この端末は16,384 tokenへ設定する。context長（最大256K設定）とは
  別の上限であり、入力履歴と出力の合計がモデルruntimeのcontext内に収まる必要がある。

APIは `POST /api/v1/chat/route` で判定結果を先に表示できるようにし、`send` には検証済み計画を渡す。
APIクライアントが直接 `mode=auto` を送った場合もサーバー側で同じ判定を行う。

## 3. 長文リアルタイム表示の修正

根本原因は `Job.events` が300件で先頭を削除する一方、購読側が現在の配列長をカーソルにしていることにある。
300件到達後は長さが増えず、新規deltaが到着しても購読側が永久に空sliceを読む。

`Job`へ単調増加する `event_sequence` と保持範囲先頭の `event_offset` を追加する。イベント取得は配列indexではなく
絶対cursorを受け取り、`events_since(cursor) -> (events, next_cursor, truncated)` を返す。
購読が保持範囲より遅れた場合、チャットはDBの最新content/thinkingをsnapshot送信してcursorを末尾へ進める。
通常のdelta順序は維持し、完了時はDBの最終snapshotを再送して、欠落・重複を最終的に解消する。
WebSocket異常切断時は最大5回の指数バックオフで再接続し、画面上は「再接続中」を表示する。

クライアントはdeltaごとの全文React state複製を避け、受信文字列をrefへ最大50ms束ねて反映する。
自動スクロールは利用者が末尾付近にいる時だけ行い、過去を読んでいる間は位置を奪わない。

## 4. ローカルASR

### 4.1 ランタイムと保存先

日本語精度を優先し、Whisperの多言語 `large-v3-turbo` モデルを、依存の少ないwhisper.cppで実行する。
公式安定版 `v1.9.1` を固定し、公式手順どおりCMakeで `whisper-cli` を構築する。
モデルは公式配布元 `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin` を使い、
1,624,555,275 bytesとSHA-256を固定検証する。軽量モデルより取得・メモリ使用量は増えるが、日本語を含む多言語の
認識品質を優先する。既存の同一モデルが検証に通る場合は再取得しない。

保存先はリポジトリ外の `data_dir()/runtimes/whisper.cpp/v1.9.1/` とする。
一時音声は `data_dir()/tmp/asr/<uuid>/` に置き、解決済みパスが同ルート内であることを確認する。
成功・失敗を問わず要求終了時に音声、WAV、文字起こしファイルを削除する。
リポジトリ内の `data/` も既にgitignore対象であり、モデルをGitへ含めない。

初回導入は `asr.install` バックグラウンドジョブで、次を行う。

1. 既存の実行ファイルとモデルを検査し、揃っていれば再利用する。
2. 一時ディレクトリへ公式タグをdepth 1で取得し、subprocess配列引数でCMake buildする。
3. モデルを `.part` へストリーム取得し、完了後にatomic renameする。
4. 実行ファイルを所有者実行可能にし、状態APIをreadyへする。

任意コマンドや利用者指定URLは受け取らない。導入操作は監査へ記録する。

### 4.2 API

- `GET /api/v1/chat/asr/status`: ready/installing/missing/error、モデル名、サイズ、再利用可否。
- `POST /api/v1/chat/asr/install-jobs`: 固定runtime/modelの導入ジョブを冪等に開始。
- `POST /api/v1/chat/asr/transcribe`: multipart音声（最大25MiB、最大45秒相当）を受け付ける。

transcribeはffmpegを配列引数で起動し、16kHz/mono/PCM S16 WAVへ正規化する。その後 `whisper-cli` を
`language=ja`、timestampなし、テキストファイル出力で最大120秒実行する。空認識は422、未導入は409、
不正media/上限超過は413/422とし、内部stderrを利用者へ露出しない。

### 4.3 ブラウザ状態機械

`idle → installing → requesting_permission → listening → transcribing → submitting → muted → idle`

- MediaRecorderとWeb Audio Analyserを併用する。RMSが閾値を超えた時点を発話開始とする。
- 発話開始後1.2秒無音、または30秒上限で録音を確定する。
- 確定後にtranscribeへ送り、非空文字列を入力欄へ反映して即送信する。
- `submitting/muted` はLLMのbusy状態と結合し、マイクボタンをdisabledにする。
- 停止操作、unmount、権限拒否、API失敗ではMediaStream track、AudioContext、timerを必ず解放する。
- HTTPSまたはlocalhost以外でgetUserMediaが使えない場合は、理由とHTTPS利用を案内する。

## 5. UI構成

- Header: assistant名、機能選択、幅を抑えた会話切替、その右端に選択会話を即時削除する44pxのゴミ箱、モデル状態、その他設定、右端に「閉じる」ラベル付きボタン。機能選択・履歴・削除は同じ行へまとめ、320pxでは2段目の全幅内で`固定幅 / 可変幅 / 44px`に配分する。削除確認は行わず、削除後は未保存の空下書きへ切り替える。空下書きは最初の送信時だけDB登録し、「新しい会話」という空履歴を残さない。
- 自動判定結果はassistant名直下の左上概略だけに表示する。従来の判定理由 + 機能選択を置いたContext barは情報が重複するため行ごと削除する。
- Messages: PCは最大960px中央、assistantは面、userはaccent、長文は可読行長と選択可能な本文。複合調査は計画と現在の手順を折り畳み表示する。
- Composer: rounded-2xlの一体型面にtextarea、左下マイク/停止、モード、右に送信。320pxでも横overflowなし。
- Settings: 段階開示し、会話名、モデル、検索設定だけを表示する。削除だけ赤を使う。

閉じる、マイク、送信は44px以上にし、aria-label、aria-pressed、aria-liveを付ける。

## 6. 受入条件

- 301件以上のdeltaを流しても停止せず、順序・欠落・重複なしで最終DB内容と一致する。
- WebSocketを途中切断しても再接続し、最終回答へ収束する。
- 自動判定の各代表文と、権限不足、曖昧なrun対象を自動テストする。
- LLM構造化判定の成功、JSON不正時のフォールバック、複合検索の重複排除・反復上限をテストする。
- ASR未導入/導入済み再利用/上限超過/変換失敗/文字起こし成功をバックエンドテストする。
- 実サービスでモデル導入、既存モデル再利用、実音声または生成した日本語音声の文字起こしを確認する。
- Chromium 1280x800と320x700でclose、mode、composer、音声状態、横overflow 0、console error 0を確認する。
- `./deck.sh test` と `frontend npm run build` を完走し、`docs/implementation-status.md`へ結果を記録する。

## 7. 参照

- whisper.cpp README: https://github.com/ggml-org/whisper.cpp/blob/v1.9.1/README.md
- whisper.cpp CLI: https://github.com/ggml-org/whisper.cpp/blob/v1.9.1/examples/cli/README.md
- 公式モデル取得スクリプト: https://github.com/ggml-org/whisper.cpp/blob/v1.9.1/models/download-ggml-model.sh
