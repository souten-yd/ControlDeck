# HTML表の根拠契約 W4a2（Draft）

ControlDeck W4a #639 の後続。NewsHub側C4で同じ座標構造を処理するための最小追加。取得成功結果の `tables` に対応する `tables_meta`:
- schema = controldeck.html-table.v1
- html_sha256 = 実際に表解析したデコード済みHTMLのUTF-8 SHA256（HTTP raw content_sha256 とは別。ブラウザの場合も解析したHTMLを使用）。
- tables_sha256 = tables のJSON（ensure_ascii=False、sort_keys=True、separators=(",",":")）UTF-8 SHA256。

table_index,row,col,rowspan,colspan は0始まりの表/セル座標。原文の文字offsetではない。th/明示scope、切り詰めの情報を維持。非表示の行・セル・明示headers=参照は現段階で表ごと保留。外部CSS・複雑なrowgroup・脚注、ブラウザで本文が増えない場合の表差分は後続。ハッシュは署名ではない。認可済みの取得結果にのみ利用し、原文配信/認証情報の共有/新規外部通信を追加しない。

既存本文と取得経路は維持。テスト追加、全体試験は未実行。NewsHubで座標を文字spanへ変換して保存しない。機器・条件・数値・列見出しの原文座標を保持し、正本保存/権限確認ができた段階で別の根拠種別として保存する。マージしない。

## 実コード接続評価
QnapNewsHub #54 の consumer `6e290bc904364fdfc43e0043cc64333fcdab192c`・quantity `0065e56b28c591d1a8538554d042bb03a76996ab` と、ControlDeck producer `7bb34d6ec06e460540f0953a83a0f6b4262a101c` の一致コピーを使用。HTML→表構造・hash→測定候補の25検査が成功。4測定の値/列/行/共有対象/条件・見出し・hash改変・隠し要素・誤ったrowspan・不正数値など。小型表100回のp50 1.421ms/p95 2.331ms（コンテナ、通信なし）。外部CSS・脚注・原文の保存、正本hash/権限を伴うDB接続、実サイト・全体pytestは未確認。マージしない。
