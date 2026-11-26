# 検索改善設計（2026-11-26）

- RFC 3986 §6 https://www.rfc-editor.org/rfc/rfc3986.html : scheme/host の正規化と URI 同値性の限界。順序付き query や path を勝手に同一視しない。
- Scrapy https://docs.scrapy.org/en/latest/topics/request-response.html : request fingerprint の用途と verbatim URL。検索候補の識別、HTTPキャッシュ、本文の同一性を別々に扱う。
- RFC 9111 https://www.rfc-editor.org/rfc/rfc9111.html : 次段のキャッシュは実URL・区分・認可・Vary・validatorをキーにし、no-store/private/認証/Set-Cookie等は保守的に除外。304は同じ表現にだけ適用、拒否状態の再試行なし。今回はキャッシュ未実装。
- Elastic RRF https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion と query aspect diversification https://www.sciencedirect.com/science/article/pii/S0306457320308517: 順位融合は候補生成、独立した観点の確保と一次資料の明示的な保護を別処理。既定切替は固定の独立評価後。

## 本PR
検索候補の同一視を保守的に変更。追跡引数は既存方針を維持、署名系を例外とし普通の節アンカーは除く。未知query・順序・www・scheme・slash・hash routeを保持。取得後に本文hashを照合する処理は別段階（未実装）。URL識別子はSSRF防御ではない。既存egress/認可/検索提供元/UI/起動は変更なし。前回6ケースに加えて署名・encoding・不正URLの回帰試験追加。全体pytest未実施。

## 単独評価結果
修正後 `_norm_url` の関数と正規表現を抜粋して Python で実行した。旧6ケースは6/6、追加の通常アンカー・署名・エンコード・hash route・不正URL等を含め17/17。リポジトリ全体のimport/pytestと実サイトの重複率は未検証。取得後のcanonical/hash統合も未実装。

## W5b-2: 順位融合の後段（Draft）
- 参考: [Elastic RRF](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)、[Santos xQuAD](https://theses.gla.ac.uk/4106/)（観点の未カバー度による多様化）。xQuADそのものではなく、RRF順位と観点別relevanceを使う簡易比較候補。
- 最大200候補/16観点、外部で検証済みの一次資料IDとrelevanceのみ。ページ自身の「公式」表記やドメインだけで昇格しない。上位10内で十分関連する一次資料は最大1件、残りは未カバー観点のsoft bonus。scoreがなければRRF順。不正・非有限scoreは拒否。
- 単独モジュールblob 80208cf0ce1756caf67cf55a6381c1e9f364acdd の人工入力で5順位ケース+4不正入力が9/9。人工relevanceを使った機能試験であり検索精度の実測ではない。既定・通信・UI・LLMは変更なし。次は既存rerankerによる観点score、一次資料の信頼できる出所確認、同一固定質問でRRF/多様化/通常検索を比較。全体pytest未実施。

## #644 と #645 は代替案（両方式の同時採用はしない）

#644 (feat/research-coverage-fusion) は観点ごとの固定枠、#645 (本PR) は関連度と未カバー観点の柔軟な加点。#644 が先に作成済みだったため、両者を独立比較して採否を決める。既定切替しない。

固定合成5問・@2（単独一次資料、複数観点、関連性の低い一次資料、弱い観点、ranker誤判定）の比較。候補と観点scoreは人工データ。実際のrerankerや本番検索の測定ではない。

|方式|Recall@2|MRR@2|nDCG@2|
|---|---:|---:|---:|
|RRFのみ|0.6667|0.7000|0.6384|
|#644 固定枠|0.7667|0.8000|0.7610|
|#645 初期の柔軟加点|0.6667|0.8000|0.7122|
|#645 関連度を基礎順位にも反映|0.8333|0.9000|0.8774|

SHA一致コピー: #644 research_fusion.py `c134669b9fe19d0c0ad49db34f35927da4c12b01`、#645 初版 `80208cf0ce1756caf67cf55a6381c1e9f364acdd`、修正版 `3162aaa84830151be17455803b9667d23d34eaf2`。Python単独実行。#645 は関連性の低い一次資料/弱い観点を過剰に昇格させない点が利点。ただし ranker 誤判定ケースは固定枠より悪い（Recall@2 .5 vs 1.0）。.6/.45 は今回の実験値、学習済み/最適化済みの値ではない。別の評価セットで閾値・重み・フォールバックを決めること。全体pytest、実検索・既存ranker接続は未実施。
