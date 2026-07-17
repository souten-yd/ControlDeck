# AIアシスタント 会話内文献レジストリ設計

## 1. 目的

Webページ、論文、レポート、資料などの調査結果を会話単位で永続化し、後続ターンから短いIDで再利用できるようにする。
長いURLや本文を毎ターンLLMへ再投入せず、利用者が指定した文献だけを展開してコンテキスト消費を抑える。

## 2. 識別子

- 表記は `R` + 36進数とする。例: `R1`〜`R9`, `RA`〜`RZ`, `R10`。
- 会話内で一意かつ単調増加とし、別会話のIDは解決できない。
- UIと回答では `[R1]`、入力では `R1` / `@R1` / `[R1]` を受け付ける。`RA` のように英字だけのIDは英単語との衝突を避けるため `@RA` または `[RA]` とする。
- URLを正規化（scheme/hostの小文字化、fragment除去、末尾slash統一）してSHA-256キーを作り、同じ会話内の重複を防ぐ。URLがない資料はタイトルとproviderを用いる。

`R` は reference を示し、単なる回答内連番と区別する。36進表記により、1,295件でも最大3文字（`RZZ`）に収まる。

## 3. データモデル

`chat_references` は次を保持する。

| 項目 | 用途 |
|---|---|
| conversation_id / sequence / short_id | 所有会話、採番順、公開ID |
| canonical_key | 会話内の重複排除 |
| kind | page / paper / document / dataset / patent / report |
| title / url / provider | 表示と出典識別 |
| excerpt | 後続ターンへ必要時だけ注入する有限長の根拠 |
| metadata_json | 将来のDOI、著者、公開年等の拡張領域 |

会話削除時はメッセージと同時に文献も削除する。所有者確認は全APIで会話を介して強制する。

## 4. 登録・引用フロー

1. Web、学術、Deep、複合調査が構造化された検索結果を得る。
2. サーバーの共通レジストリがURL等で重複排除し、短いIDを割り当てる。
3. LLMへ渡す一時的な検索根拠にも永続IDを付け、回答へ `[R1]` 形式で引用させる。
4. メッセージの `meta.sources` とWebSocketのsourcesイベントにも同じIDを含める。
5. UIは出典カードにIDを表示し、「参照」操作で次の入力へIDを挿入する。

Deep Search内部の一時的な `[1]` は、保存前に対応する永続IDへ変換する。

## 5. 後続ターンとコンテキスト制御

- 送信文から最大12件の文献IDを抽出する。
- 指定された同一会話の文献だけをDBから解決し、タイトル、種別、provider、URL、抜粋をsystem contextへ加える。
- 展開する文献コンテキストは合計18,000文字、各保存抜粋は6,000文字を上限とする。
- 文献一覧や全抜粋を通常ターンへ自動注入しない。過去回答の短いIDは通常履歴のまま保持する。
- 存在しないIDや他会話のIDは注入せず、情報漏えいを防ぐ。

これによりLLMのtool calling対応有無に関係なく動作する。Ollama、llama.cpp、ROCm上のOpenAI互換サーバー等で同じ経路を使う。

## 6. 文献解決ツールAPI

- `GET /api/v1/chat/conversations/{conversation_id}/references`: 軽量カタログ
- `GET /api/v1/chat/conversations/{conversation_id}/references/{reference_id}`: 1件を本文付きで解決
- `POST /api/v1/chat/conversations/{conversation_id}/references/resolve`: 最大12件を一括解決

APIと自動コンテキスト注入は同じ `reference_registry` を利用する。将来provider固有のfunction callingを追加する場合も、ツール実体は一括解決APIまたは同じサービス関数へ接続し、LLMランタイム層へDB責務を持ち込まない。

## 7. UI

- 出典件数の横に「会話内文献」表記を置く。
- 各資料の先頭に選択可能な `[R英数字]` バッジを表示する。
- 「参照」ボタンで入力欄へ `[R1] ` を追加し、利用者がそのまま質問を書けるようにする。
- 320px幅でもID、タイトル、操作が横溢れしないよう、タイトル行は省略表示し操作は固定幅にする。

## 8. 評価基準

- 同一会話の重複URLには同じID、別URLには連続した36進IDが付く。
- 同じIDを後続送信すると、対応する抜粋だけがLLM履歴へ注入される。
- 他ユーザー・他会話から一覧/個別/一括解決できない。
- 会話削除で文献も削除される。
- 検索回答、履歴復元、WebSocket sourcesでIDが失われない。
- PC幅と320px幅で出典カードを操作できる。
