# 検索改善設計（2026-11-26）

- RFC 3986 §6 https://www.rfc-editor.org/rfc/rfc3986.html : scheme/host の正規化と URI 同値性の限界。順序付き query や path を勝手に同一視しない。
- Scrapy https://docs.scrapy.org/en/latest/topics/request-response.html : request fingerprint の用途と verbatim URL。検索候補の識別、HTTPキャッシュ、本文の同一性を別々に扱う。
- RFC 9111 https://www.rfc-editor.org/rfc/rfc9111.html : 次段のキャッシュは実URL・区分・認可・Vary・validatorをキーにし、no-store/private/認証/Set-Cookie等は保守的に除外。304は同じ表現にだけ適用、拒否状態の再試行なし。今回はキャッシュ未実装。
- Elastic RRF https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion と query aspect diversification https://www.sciencedirect.com/science/article/pii/S0306457320308517: 順位融合は候補生成、独立した観点の確保と一次資料の明示的な保護を別処理。既定切替は固定の独立評価後。

## 本PR
検索候補の同一視を保守的に変更。追跡引数は既存方針を維持、署名系を例外とし普通の節アンカーは除く。未知query・順序・www・scheme・slash・hash routeを保持。取得後に本文hashを照合する処理は別段階（未実装）。URL識別子はSSRF防御ではない。既存egress/認可/検索提供元/UI/起動は変更なし。前回6ケースに加えて署名・encoding・不正URLの回帰試験追加。全体pytest未実施。

## 単独評価結果
修正後 `_norm_url` の関数と正規表現を抜粋して Python で実行した。旧6ケースは6/6、追加の通常アンカー・署名・エンコード・hash route・不正URL等を含め17/17。リポジトリ全体のimport/pytestと実サイトの重複率は未検証。取得後のcanonical/hash統合も未実装。
