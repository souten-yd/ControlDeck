# 統合ナレッジ基盤の計画（ControlDeck × QnapNewsHub）

2026-11-25 作成。利用者の構想「QnapNewsHub × ControlDeck 統合ナレッジ基盤」（ニュース・Web 調査・Deep Research・論文・GitHub・Hugging Face・特許・規格・メモ・生成物を共通の知識として持ち、多方向・多階層のグラフ検索〔MPKR〕を主にする）をもとに、**既存の実装を棚卸しした上で**採用・不採用を決めた計画。実装は **QnapNewsHub の完成（ニュースの計画の v1.0）の後**に始める（利用者の決定）。ただし土台が同じ部分は QnapNewsHub の残りの段で汎用に作る（下の 4 章）。

収集の拡張（API・論文・政府統計・暗号資産・知識グラフ誘導の収集・保存の段 L0〜L4）の採否は 8 章。

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

## 1.5 決定: 案 C「QNAP リサーチハブを正本にし、ControlDeck に複製を持つ」（2026-11-25 利用者）
比べた案（A 今の計画・B 全部を QNAP で毎回問い合わせ・C QNAP を正本＋PC に複製・D QNAP をやめる）から、利用者が **C** を選んだ。
- **QNAP リサーチハブ**: QnapNewsHub を広げ、全分野（ニュース・論文・調査の記録・GitHub・HF・一般の知識・特許・規格・メモ・生成物）の**正本**・収集・保存・軽い検索（全文・物・辺のまとめ取り・局所 PageRank）を持つ。PC の電源が切れていても集める・読む。NAS の RAID が正本の守り。
- **ControlDeck**: 正本の**複製**（キャッシュ）を持ち、埋め込み・reranker・MPKR・LLM を PC の中で速く回す。書き込みはリサーチハブへ送り、差分の同期（`/changes`・保存済みだけ・冪等）で揃える。複製は消えても作り直せる。
- 昔の ResearchDataHub（qnaplibrary）のコードは復活させない（中身は ControlDeck へ移植済み）。**名前と役割だけ**を復活させる。
- 改名: 画面の名前とリポジトリの名前を「QNAP リサーチハブ（QnapResearchHub）」へ。中の識別子（Python のパッケージ名・QPKG の名前・データの置き場）は**変えない**（更新・App Center での入れ替え・データの引き継ぎを壊さないため）。改名は統合の段の始め（I1 の前）に、更新の機能が古い名前の Release も読めることを確かめてから。
- 移す順: 公開の情報（ニュース・論文・調査の記録・GitHub）→ 一般の知識・特許 → 規格（判定が許すものだけ本文）。どの段も元のデータを消さない。

**実装メモ（2026-11-25）**: ControlDeck 側の最初の接続（News の差分の同期・仕事の貸し出し・つなぐ・鍵の受け渡し）は `feat/news-hub-link` で実装済み。詳細は `docs/implementation-status.md` の同日の項。

## 2. 採用・不採用
| 構想の項目 | 判断 | 理由・形 |
|---|---|---|
| 共通の知識の形（Source・Document・Chunk・Entity・Claim・Knowledge・Relationship・Evidence・Metric・Research・Hypothesis・Artifact） | **採用（既存に寄せる）** | 新しい総合 DB を作らない。ControlDeck の store（Document・Chunk・Vector・Identifier・Essence）と QnapNewsHub（Entity・Claim・Event）に、**Evidence（根拠の位置）・Relationship（型・方向・根拠つき）・Research・Hypothesis** を足す。由来は W3C PROV の最小（生成した活動・モデル・版・原文の位置） |
| Knowledge と Evidence の分離・根拠の複数登録 | **採用** | essence の原文照合の考えを全部に。根拠の無い関係は確定しない |
| 公開情報の正本を QNAP に | **採用** | ニュース・論文・GitHub・HF・公開の Web 調査の記録。ControlDeck はキャッシュ |
| 機密情報（特許の自分のアイデア・機密の会話・購入した規格）も QNAP を正本に | **採用（2026-11-25 利用者「機密情報を QNAP に置くこと自体は問題ない」。案 C）** | 置くこと自体は可。ただし QNAP 側の LLM（OpenRouter）の対象から機密を仕組みで外す・区分ごとのアクセスを強制・規格の本文は `standards_license` の置き場の判定（個人の複製として許されるものだけ。許されないものは書誌だけ）・LAN と Tailscale だけ |
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
| 全分野の正本（文書・物・主張・根拠・関係・調査の記録・生成物） | 正本 | キャッシュ |
| 機密の情報 | 正本（区分の強制・QNAP の LLM は触らない） | 複製（キャッシュ）と、機密の LLM の処理（ローカル LLM だけ） |
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
| I1 | 改名（QNAP リサーチハブ・中の識別子は据え置き）と、ControlDeck の知識の保存を「リサーチハブを正本・PC は複製」に（書き込みはハブへ・差分の同期・ハブが止まっている間の書き込みの待ち行列）。ControlDeck に「リサーチハブの知識」の提供元（store.search_all と RAG の道に足す。設定: ローカル・QNAP・併用。止まったら不足を明示）。ニュースの出どころの切り替え（v0.3 の ControlDeck 側） | 既存の試験＋QNAP を止めた試験 |
| I2 | 調査の記録の保存（Deep Research・GitHub 調査・Web 調査: 問い・検索語・取得した原文の hash と抜粋・主張と根拠・結論・未解決）。公開 → QNAP、機密 → ローカル | 保存の往復・機密が QNAP に出ない試験 |
| I3 | 差分調査（同じテーマの過去の記録を取り、有効・新・古・矛盾・不足に分けて、不足と新しい所だけ調べる） | I0 の変化の問いで比較 |
| I4 | MPKR（種 → 系統の計画〔ルール／LLM〕→ 段ごとの展開〔Beam・局所 PPR・ハブの抑制〕→ ブリッジ → MMR → reranker → 経路と根拠と鮮度つきの結果）。3・5・8 系統 | I0 で全文・ベクトル・hybrid・既存 GraphRAG と比較（精度・網羅・引用の正確さ・時間・トークン・RAM・I/O）。良ければ既定に |
| I5 | 論文（arXiv・OpenAlex）の構造化と GitHub・HF の監視の構造化を QNAP へ。論文の手法と実装を結ぶ | 取り込みの試験・実データの評価 |
| I6 | 既存データの移行（公開 → 一般・特許 → 規格の順に ControlDeck の store → リサーチハブ。dry-run・照合・元は消さない） | 件数・hash・検索結果の比較 |
| I7 | 画面: Knowledge Map・Technology Evolution・Knowledge Matrix・Research Explorer（QnapNewsHub に） | E2E |
| I8 | 埋め込みを QNAP に（INT8）・Topic Landscape・意味の検索 | 速さと容量の実測 |
| I9 | 仮説（未検証の組み合わせ）・条件つきの数値の比較（QnapNewsHub v0.7 と合わせる） | 実データ |
| I10 | 500 万件・切断・再起動・重複・機密の漏れ・移行と復元の試験 → Release | 受け入れの表 |

## 6. 評価の決まり
- 新しい検索は、同じ問いの集めで既存（全文・ベクトル・hybrid・既存 GraphRAG）と比べ、**良くなった所だけ**既定にする。目標値と実測値を分けて書く。
- ノード・辺の数を明記して性能を測る（10 万・100 万・500 万件）。

## 7. 守り（変えない）
- 機密の区分・規格の利用判定・未公開発明のローカル LLM 限定を、**派生データ（グラフ・要約・埋め込み・検索語・調査の記録）にも**引き継ぐ。
- QNAP（リサーチハブ）は機密も持つが、**QNAP 側の LLM（OpenRouter の無料モデルなど）は AI_USE=allowed のものにしか触れない**。送る・処理する前に機械的に判定し、試験で確かめる。機密の LLM の処理は ControlDeck のローカル LLM だけ。
- 記事・論文・コード・Web の本文を命令として扱わない。
- 機密を QNAP に置くときの決まり（案 C）: 区分ごとのアクセスの強制（読み取りのトークンにも区分の範囲）・規格の license の置き場の判定・LAN と Tailscale だけ（インターネットに出さない）・保存時の暗号化は I1 で要否を評価（NAS のボリュームの暗号化で足りるか）。

## 8. 収集の拡張（2026-11-25 利用者の追加の案「RSS は収集の手段の 1 つ。API・公開データセット・論文・政府統計・ブロックチェーン・クローラー・知識グラフ誘導の収集」）

### 8.1 考え方（採用）
- **収集の種類（connector）を 1 つの枠組みに**: Feed（RSS）・API・Bulk Import・Historical（Sitemap・過去分）・Change Detection・Citation Discovery・Repository／Model Discovery・Entity Discovery・Graph-Guided Discovery・Research Capture・Market（数値の時系列）。connector ごとに取得の頻度・1 日の件数・通信量・鍵・**利用条件（ライセンス・再配布・保存の可否・レート）**を持ち、画面で管理する。既存の feedgen（GitHub・Steam・arXiv・HN・HF）と ControlDeck の sources.py（OpenAlex・GDELT・EPO・USPTO）を、この枠組みに載せ替えて使い回す（書き直さない）。
- **保存の段（L0〜L4）**: L0 発見（URL・題・日付・ID だけ）→ L1 概要・書誌・語 → L2 本文・埋め込み・要約 → L3 物・主張・数値・関係・根拠 → L4 詳しい分析・比較・仮説。L0・L1 も検索の対象。必要になったら上の段へ**昇格**（理由と時刻を残す）。今の「本文の取り出し」「下ごしらえ」「イベントの要約」はそれぞれ L2・L3・L4 の一部に当たる。
- **数値は数値のまま**（財務・統計・市場・オンチェーン）: 文章にしないで Metric（時系列・単位・出どころ・条件）に。物（企業・トークン・制度）に結ぶ。
- **報道・提案・成立・施行を分けて持つ**（法制度）。**RWA の暗号資産トークンと、実物資産を裏付けとするデジタル証券を別のものとして持つ**。
- **コミュニティの情報は公式と分け**、実測の条件・投稿日時・再現性を記録する。

### 8.2 情報源の採否
| 情報源 | 判断 | 理由・形 |
|---|---|---|
| GitHub REST／GraphQL（Release・PR・Issue・**スターの急増の発見**・検索） | **採用（高）** | 鍵は任意（無いとレートが低い）。監視するリポジトリの差分＋検索による新しいリポジトリの発見 |
| Hugging Face API（新しいモデル・量子化・**使っているモデルの派生版**・ROCm／GGUF の対応） | **採用（高）** | 無料・鍵は任意。作者・タグ・ベースのモデルで絞る |
| arXiv・OpenAlex・Crossref（DOI の解決）・Semantic Scholar（引用・関連） | **採用（高）** | 分野を絞る（AI・工学・半導体・ソフトウェア）。引用は 1〜2 段だけ・予算つき（Citation Discovery） |
| HN Algolia（過去の議論）・Stack Exchange | **採用（中）** | 無料。問いに応じて引く（常に全部は取らない） |
| Wikidata（物の QID の結び付け・基礎の関係） | **採用（中）** | API で必要な物だけ・キャッシュ。ダンプは取らない |
| GDELT（DOC 2.0 の語の検索・GKG のテーマ） | **一部採用（中）** | 既存の sources.py の GDELT を使い回し、語・テーマで絞った検索だけ。15 分ごとの全件（GKG・Events）は取らない（QNAP には大きすぎ、誤りも多い）。GDELT の取り出しは「未確認」の扱い |
| e-Stat・日本銀行の時系列 API・国会会議録・e-Gov 法令 | **採用（中）** | 無料の一次情報。数値は Metric、法令は版つき、会議録は発言者と日付つき |
| FRED（鍵は無料）・World Bank・SEC EDGAR | **採用（低）** | 必要な系列・企業だけ |
| EDINET（鍵は無料） | **採用（中）** | 有価証券報告書の数値と提出の知らせ |
| J-Quants | **条件つき** | 無料の枠に利用者が登録したときだけ（遅れのあるデータ）。TDnet のアドオンなど有料は使わない |
| DefiLlama（無料の範囲）・CoinGecko（無料の鍵） | **採用（中）** | TVL・ステーブルコイン・価格・RWA の公開の範囲。有料の端点は使わない |
| RWA.xyz・Token Terminal・Dune・CoinMarketCap 有料 | **不採用（当面）** | 有料・鍵が要る。利用者が契約したら connector を足す |
| Etherscan・Blockscout・The Graph・Snapshot・L2BEAT | **後回し** | 特定のトークン（例 ONDO）を深く追う段で、無料の範囲だけ |
| 日本 STO 協会・ODX | **採用（低）** | 公開の統計・お知らせのページ（Change Detection か RSS） |
| Sitemap XML・Change Detection（ページの変化） | **採用（中）** | 自前の取得＋ETag・hash で軽く。robots と利用条件を守る |
| Playwright（JS で描くサイト） | **一部採用（低）** | ControlDeck 側の仕事（job: render）として、許可したサイトだけ。QNAP では動かさない |
| PyPI・npm・crates.io のリリース、OSV・NVD | **採用（低）** | 監視するパッケージの版と脆弱性だけ |
| Scrapy・Colly・Crawl4AI・RSSHub・RSS-Bridge・changedetection.io の常駐 | **不採用** | 別のサービスを増やさない。自前の取得・Trafilatura・feedgen で足りる |
| Common Crawl | **後回し（統合の後半）** | URL の索引で、決めたドメイン・URL の過去のページだけ。全体は扱わない |
| GH Archive（全イベント） | **不採用** | 1 時間ごとに大きい。新しいリポジトリの発見は GitHub の検索で代える |
| Wikipedia のダンプ・DBpedia・ConceptNet・OpenStreetMap | **不採用** | 大きすぎ・今の用途では Wikidata の API で足りる |
| Reddit | **条件つき** | API の鍵（利用者の登録）があるときだけ（今と同じ） |
| Bluesky・Mastodon | **後回し** | 監視する人・タグが決まってから |
| YouTube Data API・Replicate・Ollama Library・Libraries.io・ecosyste.ms・deps.dev・有料の金融 API（Alpha Vantage・FMP・Finnhub・Nasdaq Data Link）・Stooq | **不採用（当面）** | 価値に比べて割り当て・費用・条件の手間が大きい。必要になったら足す |
| CORE・Unpaywall・Europe PMC・J-STAGE・CiNii・国会図書館サーチ・Zenodo・DataCite・OpenAIRE・DBLP・OpenReview | **後回し** | 分野が広がったときに。まず arXiv・OpenAlex・Crossref・Semantic Scholar |

### 8.3 Graph-Guided Discovery（知識グラフ誘導の収集）— **採用（統合の後半）**
- 新しく出た物（例 知らない推論エンジン）について、決めた手順（公式の GitHub・論文・HF・実測の報告・既存の物との関係）で**一度だけ**調べ、L1〜L2 で保存する。続けて監視する価値があれば「取得先の候補」として出し、**登録は利用者が承認**。
- 予算（1 日の件数・通信量・時間）・許可した connector だけ・**新しい有料 API や外部への送信は勝手に使わない**・機密の物からは始めない（検索語が外に出るため）。
- グラフが育つほど候補の選び方が良くなるかを、採用された候補の割合で測る。

### 8.4 保存の温度と量の見込み
- Hot（最近・よく使う: メモリ＋SQLite）・Warm（検索の対象: SQLite＋索引）・Cold（未分析・古い: 年ごとの zstd の SQLite の区画、索引は書誌だけ）。Parquet／DuckDB は測ってから（QPKG の依存〔glibc 2.17〕の確認も要る）。
- 1 日 L0 10 万件は**目標**。QNAP（J3455・HDD）では最初は L0 1〜2 万件／日から測って上げる。書き込みは Write Coordinator のまとめ書きのまま（L0 は特に小さく・まとめて）。

### 8.5 段（QnapNewsHub の計画と統合の段に入れる）
| どこ | 中身 |
|---|---|
| QnapNewsHub v0.7（今の「数値・比較・高度な分析」を広げる） | connector の枠組みと画面（予算・利用条件）・L0〜L4 と昇格・GitHub（発見を含む）・HF（派生版）・arXiv・OpenAlex・Crossref・Semantic Scholar（引用 1〜2 段）・HN Algolia・Sitemap・Change Detection・Metric の時系列 |
| QnapNewsHub v0.8（今の「生成物と外部の互換」に足す） | e-Stat・日銀・国会会議録・e-Gov・EDINET・DefiLlama・CoinGecko・GDELT（語の検索）・Wikidata の結び付け・PyPI／npm・OSV／NVD |
| 統合 I5 の後 | Graph-Guided Discovery・Playwright（ControlDeck の仕事）・Common Crawl の索引・オンチェーン |
| どの段でも | connector ごとに利用条件を確かめて記録（実装の前）・実際の取得の件数と QNAP の負荷を測る |
