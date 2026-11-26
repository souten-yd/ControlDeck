# 検索改善設計（2026-11-26）

- RFC 3986 §6 https://www.rfc-editor.org/rfc/rfc3986.html : scheme/host の正規化と URI 同値性の限界。順序付き query や path を勝手に同一視しない。
- Scrapy https://docs.scrapy.org/en/latest/topics/request-response.html : request fingerprint の用途と verbatim URL。検索候補の識別、HTTPキャッシュ、本文の同一性を別々に扱う。
- RFC 9111 https://www.rfc-editor.org/rfc/rfc9111.html : 次段のキャッシュは実URL・区分・認可・Vary・validatorをキーにし、no-store/private/認証/Set-Cookie等は保守的に除外。304は同じ表現にだけ適用、拒否状態の再試行なし。今回はキャッシュ未実装。
- Elastic RRF https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion と query aspect diversification https://www.sciencedirect.com/science/article/pii/S0306457320308517: 順位融合は候補生成、独立した観点の確保と一次資料の明示的な保護を別処理。既定切替は固定の独立評価後。

## 本PR
検索候補の同一視を保守的に変更。追跡引数は既存方針を維持、署名系を例外とし普通の節アンカーは除く。未知query・順序・www・scheme・slash・hash routeを保持。取得後に本文hashを照合する処理は別段階（未実装）。URL識別子はSSRF防御ではない。既存egress/認可/検索提供元/UI/起動は変更なし。前回6ケースに加えて署名・encoding・不正URLの回帰試験追加。全体pytest未実施。


## W5b-2 観点網羅と一次資料保護
RRF は候補の順位を融合し、観点別の上位候補から最小枠を確保する。一次資料は呼び出し側が根拠を確認した識別子だけを、観点内の上位3件にある場合に保護する。自己申告 metadata で優遇しない。共通する候補が既に上位3件の観点を満たせば余分な枠を消費しない。最大64リスト、200結果、観点枠0〜3。順序は呼び出し側の観点優先順。
これは xQuAD の観点網羅の考え方を参考にした軽量な候補で、論文アルゴリズムそのものではない。既定の検索経路には接続しない。観点別 Recall、一次資料 Recall、nDCG、通常検索の非劣化を独立した実データで比較し、採否を決める。全体試験・実機未確認。一次資料の検証器・キャッシュ・rerankerとの接続は別段階。

## W5b-2 単独試験
`research_fusion.py` の Git blob SHA `c134669b9fe19d0c0ad49db34f35927da4c12b01` 一致コピーをPythonで実行。合成の hardware/power 2観点、正解 official/power、上位2件: RRF は popular/power（Recall@2 0.5）、観点/一次資料保護版は official/power（Recall@2 1.0）。aspect_floor=0でRRF同等、自己申告優遇なし、グループ重複でも同じ結果の4 assert通過。別の合成3観点例も出力を確認。これは限定的な機能試験で、全体の検索精度・公式資料の検証器の性能を示さない。共通候補の局所上位への出現だけでは意味的な観点充足は保証しないため、独立正解で評価する。
