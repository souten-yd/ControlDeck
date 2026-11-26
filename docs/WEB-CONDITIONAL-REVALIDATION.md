# W5c: HTTP再検証と変更検知（Draft）

## 参考と設計
- RFC 9111 HTTP Caching §4.3、RFC 9110 HTTP Semantics §13: https://www.rfc-editor.org/rfc/rfc9111.html / https://www.rfc-editor.org/rfc/rfc9110.html
- Conditional GETを使う。ETag/Last-Modifiedは本文の正本hashではなくHTTPのvalidator。304のとき元の取得時刻と本文hashを維持し、再検証時刻を別に持つ。
- 一回の認可済み調査処理に閉じたメモリキャッシュ。既定OFF。16件、JSON換算8MiB、600秒の上限。並行要求のepochで古い応答による復元・上書きを拒否。実メモリ使用量はJSON換算より大きい。
- URLは完全一致（検索用のURL正規化キーは使わない）。静的HTTPS・標準egress・raw HTMLなしだけ。クエリ、fragment、userinfo、資格情報を示すパス名、カスタムポートを対象外。抽出mode、文字数・バイト上限、UAもキーに含む。
- 明示Cache-Control: publicと妥当なvalidatorが必須。private/no-store/Vary/Set-Cookie/認証challenge/部分応答は保存しない。304にもpublicと整合するvalidatorを求める（一般HTTPキャッシュより保守的）。redirect先へのvalidator転送なし。拒否・失敗・不整合304で過去の本文を返さない。通信は従来のguarded_clientに限定。
- 200応答で取得済みの復号済み本文bytesのSHA256とContent-Typeが同じ場合、抽出だけ再利用し取得日時を更新。異なる場合は通常の抽出。304は元のhttp_status/本文/hash/fetched_atを保持、revalidation_http_statusとrevalidated_atを別に返す。
- 呼び出し側への接続は未実装。全ユーザー共有キャッシュ、永続化、スケジューラ、外部通信経路追加、認可省略、UI既定変更、LLM再解析の自動化なし。#646の後続。

## 評価・残課題
- 2026-11-26: revalidation.py のblob SHA `a01049f8206802b0be2d5a1c05d6b7b99fa17ad8` と一致する単独モジュールをPythonで実行し、34検査成功（対象URL、ヘッダー制約、validator、LRU/期限、世代管理、コピー分離等）。
- fetch.collectのmock HTTP試験をtest_web_revalidation.pyに追加。200→304、変更応答、拒否、redirect、抽出設定分離、同一bytes等。全体pytest、実サイトでの304率/転送量/速度、実際の調査処理の統合は未実施。
- GitHub直接cloneはDNS制約により未実施。実機・統合試験の結果により採否を決める。キャッシュにヒットしないときも既存の取得経路を通す。アクセス制限回避やstale fallbackは追加しない。マージしない。
