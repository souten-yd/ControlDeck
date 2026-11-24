# 統合ナレッジ基盤の計画（ControlDeck × QnapNewsHub）

2026-11-25 作成。利用者の構想「QnapNewsHub × ControlDeck 統合ナレッジ基盤」（ニュース・Web 調査・Deep Research・論文・GitHub・Hugging Face・特許・規格・メモ・生成物を共通の知識として持ち、多方向・多階層のグラフ検索〔MPKR〕を主にする）をもとに、**既存の実装を棚卸しした上で**採用・不採用を決めた計画。実装は **QnapNewsHub の完成（ニュースの計画の v1.0）の後**に始める（利用者の決定）。ただし土台が同じ部分は QnapNewsHub の残りの段で汎用に作る（下の 4 章）。

関連: `docs/PLAN-knowledge-platform.md`・`docs/HANDOFF-knowledge-platform.md`（ControlDeck の知識基盤）、QnapNewsHub の `docs/ARCHITECTURE.md`・`docs/HANDOFF.md`。

## 1. 棚卸し（origin/main・2026-11-25）
| 既にあるもの | 中身 | 使い方 |
|---|---|---|
| 統合ストア `knowledge/store.py` | 分野別 SQLite（general・patents・standards・news）。documents・bodies・chunks・vectors（モデル名・次元つき）・identifiers（DOI・特許番号・規格番号）・essences。既定 confidential、AI_USE（prohibited・local_only・allowed）。検索は全文（日本語 2-gram）＋ベクトルの RRF | **そのまま土台**。新しい情報源もこの形に載せる |
| 保存先 `knowledge/storage.py` | 置き場の設定。**SQLite を NFS/SMB に置かない** | 守る。QNAP は API 越し |
| GraphRAG `workflows/rag_graph.py` | コレクションごとの三つ組の表（s・p・o・chunk_id）、1 ホップ | MPKR の材料の 1 つ |
| 知識マップ `knowledge/map.py` | 書誌の関係（引用・版・根拠）、1〜2 hop、LLM なし | MPKR の辺の種類に入れる |
| エッセンス `knowledge/essence.py` | 8 種の要素をローカルモデルで、原文と一字一句照合、model_inference・unreviewed で保存 | 「AI 推論と原文の区別」の手本。論文・ニュースにも広げる |
| 取り込み `ingest.py`・`pdf_ingest.py`・`sources.py` | PDF・図表の VLM 記述・OpenAlex（論文の書誌）・EPO・USPTO・RSS・GDELT | 論文の構造化はここを伸ばす |
| 機密 `standards_license.py`・search_all | 規格の利用判定（fail-closed）、クラウドへ会話外の confidential を出さない | **全部の派生データに引き継ぐ** |
| QnapNewsHub | 記事・物（entities）・facts（claims）・イベント・集計・取り出しの版・Write Coordinator・お休み・署名つき更新 | 公開情報の正本の候補 |
| **無いもの** | Deep Research・GitHub 調査・Web 調査の**結果の保存**（返すだけ。会話の出典に残るだけ）、arXiv の取り込み、GitHub・Hugging Face の構造化、3 ホップ以上の探索、検索の比較評価 | この計画で作る |

## 2. 採用・不採用
| 構想の項目 | 判断 | 理由・形 |
|---|---|---|
| 共通の知識の形（Source・Document・Chunk・Entity・Claim・Knowledge・Relationship・Evidence・Metric・Research・Hypothesis・Artifact） | **採用（既存に寄せる）** | 新しい総合 DB を作らない。ControlDeck の store（Document・Chunk・Vector・Identifier・Essence）と QnapNewsHub（Entity・Claim・Event）に、**Evidence（根拠の位置）・Relationship（型・方向・根拠つき）・Research・Hypothesis** を足す。由来は W3C PROV の最小（生成した活動・モデル・版・原文の位置） |
| Knowledge と Evidence の分離・根拠の複数登録 | **採用** | essence の原文照合の考えを全部に。根拠の無い関係は確定しない |
| 公開情報の正本を QNAP に | **採用** | ニュース・論文・GitHub・HF・公開の Web 調査の記録。ControlDeck はキャッシュ |
| **機密情報（特許の自分のアイデア・機密の会話・購入した規格）も QNAP を正本に** | **不採用（当面）** | 規格は license で共有サーバー・埋め込みが禁止のものがある。特許の未公開発明はローカル限定の決まり。QNAP に暗号化と区分の強制が揃うまで ControlDeck のローカルのまま。将来の再判断の条件を 7 章に |
| 多方向・多階層グラフ検索（MPKR）を主に | **採用（評価で決める）** | ただし「主にする」のは比較評価（6 章）で既存より良いと示してから。最初は既存の hybrid の上位の道として |
| MPKR の置き場 | **ControlDeck に 1 つ** | 計画・融合・reranker・埋め込みは GPU のある ControlDeck。QNAP は「種の検索・辺のまとめ取り・局所 PageRank」の軽い道具だけ（同じ探索を 2 か所に書かない） |
| 探索の系統 3・5・8・12 | **一部採用** | 3・5・8 を実装して比較。12 は作らない（評価で 8 が足りないと分かれば足す）。既定は 5 系統・深さ 3・候補 200、早く足りたら止める |
| 系統の決め方（LLM・ルール） | **採用** | 簡単な問いはルール（物の種類から既定の方向）で LLM を呼ばない |
| Beam Search・局所 PageRank・MMR・ブリッジノード・ハブの抑制 | **採用** | 上限（候補・深さ・時間・通信・文脈）つき |
| 差分調査（Incremental Research） | **採用（優先度 高）** | 調査の記録を保存し、同じテーマで「有効・新しい・古い・矛盾・根拠不足・未解決」に分けて差分だけ調べる |
| 調査の結果の永続保存 | **採用（前提）** | 今は保存していない。保存する先: 公開の調査 → QNAP、機密の会話の調査 → ControlDeck の store（confidential） |
| 論文の構造化（課題・手法・実験・結果・制約・実装・引用・再現性） | **採用** | OpenAlex の経路を伸ばし arXiv を足す。エッセンスの 8 種と対応づけ、手法と GitHub の実装を結ぶ |
| GitHub の構造化（README・Release・PR・Issue・コミット・LICENSE） | **一部採用** | 監視するリポジトリだけ。コードの全保存はしない。SHA で差分。「予定・PR・main・Release」を別の状態に |
| Hugging Face（モデル・量子化・ライセンス・性能） | **採用（軽く）** | 監視するモデル・作者だけ。カードの要点と数値（Metric） |
| 階層（L0 原文〜L4 分野） | **採用（既存に当てはめる）** | L0 chunk・L1 文書の要約・L2 イベント・L3 物のカード／話題・L4 分野のまとめ（日刊・週刊）。影響した所だけ更新 |
| Topic Landscape（埋め込みの地図） | **後回し** | 埋め込みが QNAP に揃ってから（I8） |
| 500 万件・数千万の辺 | **設計は採用・試験は段階** | 分野ごとの DB に分ける・隣接リストのキャッシュ・段ごとのまとめ取り。試験は 100 万 → 500 万 |
| 既存データの移行 | **採用** | dry-run・バックアップ・冪等・件数と hash の照合・元を消さない。原文の無いものは「原文なし」と明示（根拠を作らない） |
| 画面を Vue に | **不採用** | QnapNewsHub は React で作ってある（ControlDeck の画面を使い回すため） |
| ControlDeck の UI・権限・実行能力の変更 | **不採用** | 既存を壊さない。設定で「ローカル・QNAP・併用」を選ぶだけ |

## 3. 役割
| 機能 | QNAP（QnapNewsHub） | ControlDeck |
|---|---|---|
| 収集（RSS・GitHub・arXiv・HF） | 主 | 止まっているときだけ |
| 公開情報の正本（文書・物・主張・根拠・関係・調査の記録・生成物） | 正本 | キャッシュ |
| 機密の情報 | 持たない | 正本（今のまま） |
| 全文・物・種の検索、辺のまとめ取り、局所 PageRank、集計 | 主 | 要求 |
| 埋め込み・reranker・LLM の分析・MPKR の計画と融合・回答 | なし | 主 |

## 4. QnapNewsHub の残りの段の組み替え（ニュースの完成までに汎用に作る）
ニュース専用に作ると、統合で作り直しになるため:
- **v0.5（知識グラフ）**: 物・主張に加えて **Document（種類: news・paper・repo・model・research・note）・Evidence・Relationship（型・方向・根拠・確かさ）** を汎用に。ニュース以外の種類は表と API だけ用意（取り込みは統合の段）。
- **v0.6（検索の統合）**: ControlDeck が使う**検索の道具の API**（種の検索〔全文・物〕・`POST /graph/neighbors`〔段ごとにまとめて・辺の型で絞る・上限〕・局所 PageRank）と、調査の記録の保存（`/research`）。MPKR 本体は ControlDeck 側（I4）。

## 5. 統合の段（QnapNewsHub の完成の後）
| 段 | 中身 | 確かめ方 |
|---|---|---|
| I0 | 評価の問いの集め（日本語の技術調査・複数文献・変化・矛盾。50 問から）と採点の道具。既存の hybrid・GraphRAG の基準値 | 基準値の表 |
| I1 | ControlDeck に「QNAP の知識」の提供元（store.search_all と RAG の道に足す。設定: ローカル・QNAP・併用。止まったら不足を明示）。ニュースの出どころの切り替え（v0.3 の ControlDeck 側） | 既存の試験＋QNAP を止めた試験 |
| I2 | 調査の記録の保存（Deep Research・GitHub 調査・Web 調査: 問い・検索語・取得した原文の hash と抜粋・主張と根拠・結論・未解決）。公開 → QNAP、機密 → ローカル | 保存の往復・機密が QNAP に出ない試験 |
| I3 | 差分調査（同じテーマの過去の記録を取り、有効・新・古・矛盾・不足に分けて、不足と新しい所だけ調べる） | I0 の変化の問いで比較 |
| I4 | MPKR（種 → 系統の計画〔ルール／LLM〕→ 段ごとの展開〔Beam・局所 PPR・ハブの抑制〕→ ブリッジ → MMR → reranker → 経路と根拠と鮮度つきの結果）。3・5・8 系統 | I0 で全文・ベクトル・hybrid・既存 GraphRAG と比較（精度・網羅・引用の正確さ・時間・トークン・RAM・I/O）。良ければ既定に |
| I5 | 論文（arXiv・OpenAlex）の構造化と GitHub・HF の監視の構造化を QNAP へ。論文の手法と実装を結ぶ | 取り込みの試験・実データの評価 |
| I6 | 既存の公開データの移行（ControlDeck の news・papers のコレクション → QNAP。dry-run・照合・元は消さない） | 件数・hash・検索結果の比較 |
| I7 | 画面: Knowledge Map・Technology Evolution・Knowledge Matrix・Research Explorer（QnapNewsHub に） | E2E |
| I8 | 埋め込みを QNAP に（INT8）・Topic Landscape・意味の検索 | 速さと容量の実測 |
| I9 | 仮説（未検証の組み合わせ）・条件つきの数値の比較（QnapNewsHub v0.7 と合わせる） | 実データ |
| I10 | 500 万件・切断・再起動・重複・機密の漏れ・移行と復元の試験 → Release | 受け入れの表 |

## 6. 評価の決まり
- 新しい検索は、同じ問いの集めで既存（全文・ベクトル・hybrid・既存 GraphRAG）と比べ、**良くなった所だけ**既定にする。目標値と実測値を分けて書く。
- ノード・辺の数を明記して性能を測る（10 万・100 万・500 万件）。

## 7. 守り（変えない）
- 機密の区分・規格の利用判定・未公開発明のローカル LLM 限定を、**派生データ（グラフ・要約・埋め込み・検索語・調査の記録）にも**引き継ぐ。
- QNAP には public（AI_USE=allowed）のものだけを送る。送る前に機械的に判定し、試験で確かめる。
- 記事・論文・コード・Web の本文を命令として扱わない。
- 機密を QNAP に置く再判断の条件: 保存時の暗号化（鍵は ControlDeck 側）・区分ごとのアクセスの強制・規格の license が許すことの判定・利用者の明示の承認。
