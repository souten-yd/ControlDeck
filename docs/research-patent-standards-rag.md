# 特許・規格の RAG — 調査・評価・採用した方式（2026-11-21）

利用者の問い（2026-11-21）:
1. 特許・規格は書誌全体が関係する。RAG に入れるときどうするか。GraphRAG が要るのでは。
2. 文脈がすべて重要なので、reranker で上位 k 件に絞る方式は合わないのでは。コンテキストが十分なら書誌の全文を入れて答えるべきでは。
3. PDF から文章・画像・波形グラフ・表を抽出して記録し、体系的（グラフ的）に引き出す規格検索の RAG。AI アシスタントと接続。

## 1. 調べたこと（要点）

| 出典 | 分かったこと |
|---|---|
| Li et al. 2024「Retrieval Augmented Generation or Long-Context LLMs?」（EMNLP Industry、Self-Route） | 資源が足りれば長いコンテキスト（LC）は平均で RAG を上回る。60% 超の質問で答えは同じ。まず RAG で答え、答えられなければ LC に回すと費用を 39〜65% 減らせる |
| Jiang et al. 2024「LongRAG」 | 検索の単位を文書全体（4K トークン以上）にし、少数（10 未満）の長い単位を読ませる。単位の数が桁違いに減り、文脈が切れない |
| Yu et al. 2024「In Defense of RAG in the Era of Long-Context LMs」（OP-RAG） | 取り出した断片は**文書の元の順**に並べると良い。断片を増やすと品質は上がってから下がる（逆 U 字）。全文を入れるより少ないトークンで良い点がある |
| Chroma 2025「Context Rot」、NoLiMa | 入力が長くなるほど、文字の一致が無い問いや紛らわしい文が混ざると正答率が落ちる。何を・どう並べて入れるか（コンテキストの設計）が要る |
| Long Context vs RAG（2501.01880）・業界の比較 | 文書全体を通した推論は LC が強い。出典を特定する事実確認は RAG が強い。位置の偏りがある。要約で探して全文を読ませる混成が増えている |
| 3GPP 規格の RAG（Telco-oRAG・Chat3GPP・TelcoAI 2026） | 規格は節の階層に沿って分割し（平らな分割は文脈が切れる）、メタデータで絞り、用語集で問いを補い、図も扱うと良い。関係する系列だけ探すと軽くなる |
| GraphRAG / LightRAG の比較（Microsoft の LazyGraphRAG ほか） | LLM で全文から知識グラフを作る GraphRAG は索引の費用がベクトル RAG の約 1000 倍、問い合わせも重い。効くのは多段の推論・全体の要約。単純な事実の検索はベクトル RAG と同等以下 |
| 特許の KG + RAG（KLIPA 2025、MemGraph SIGIR 2025） | 特許の知識グラフと RAG の併用は有望。ただし請求項の対比・審査官の引用グラフを組み合わせた確立した方式はまだ無い |
| JAPIO 年報 2024・2025、知財・情報フェアの比較 | 生成 AI の特許調査は、AI の出力を人が請求項と引用文献で突き合わせる前提。精度を同じ条件で比べた資料は見当たらない |
| UniDoc-Bench（Salesforce 2025）、生物医学の MM-RAG 比較（2512.16802） | 図・表は「文章 + 図表の説明（キャプション）」を同じ候補に入れる融合が、文字だけ・画像だけより良い。画像のまま探す ColPali 系は互角だが優位とは言えない。OCR だけでは図の意味が落ちる |

## 2. 採用した方式

### 2.1 文書を単位にする（特許・規格）

- 探すのは従来どおり断片（2-gram 全文 + ベクトル + RRF）。**断片の点数を文書ごとに集め、文書を並べる**（LongRAG の考え）。
- reranker は「どの文書を読むか」を決めるために使い、**選んだ文書の中身を削るためには使わない**。

### 2.2 入るなら全文、入らなければ骨格と該当箇所（Self-Route / OP-RAG）

モデルのコンテキストの長さから、資料に使える文字数（`context_budget`）を決める。

1. 上位の文書の全文の合計が予算に入るなら、**全文を元の順で**入れる。
2. 入らなければ、文書ごとに**骨格**（特許: 書誌・要約・**請求項の全文**、規格: 番号・題名・版・適用範囲・引用規格）と、**当たった節を元の順**に、親の見出しを付けて入れる。
3. 文書の並びは関係の強い順、文書の中は元の順。問いは最後に置く（位置の偏りを避ける）。
4. どちらの方式で入れたかを回答の記録に残す。

### 2.3 書誌の関係は「確定した関係のグラフ」で持つ（LLM の GraphRAG は使わない）

- 文書ごとに**識別子**（公報番号・規格番号と版・DOI）と、本文に出てくる**参照**（引用文献・引用規格）を規則で抜き出す。
- 関係は「引用する」「引用規格」「同じ識別子の別の版（置き換え）」。根拠の位置を持つ。
- 検索では上位の文書から **1 hop** だけ広げ（件数に上限）、つながった文書を候補に足す。
- LLM で三つ組を作る従来の GraphRAG（`rag_graph.py`）は一般のコレクション向けに残す。特許・規格の確定した関係には使わない（費用と誤り）。
- 発明の本質・課題・解決原理のような意味の関係は、PatentBuddy の Draft（知識エッセンス）を後で再評価する（K-PR7）。

### 2.4 規格は節の階層で分割し、番号で引ける

- 「4.2.1 …」のような条項見出しで分割し、断片に条項番号と見出しを持たせる。
- 規格番号は正規化して索引にし、番号での完全一致を最優先にする。版と状態（現行・旧版）を持つ。
- 本文の保存・埋め込み・LLM への入力は `standards_license.evaluate` が許すときだけ。許されないものは番号・題名・版・条項番号だけで探せる。

### 2.5 PDF は文章と図表の説明を一緒に持つ（UniDoc-Bench）

- ページごとの文章（Poppler）。文字の少ないページは OCR（Tesseract 日本語・英語）。
- 埋め込み画像とページの画像を原本と別に保存（ハッシュ名）。
- 図・表・グラフのあるページは、ローカルの VLM に「図の内容・表の中身（Markdown）・グラフの軸と単位と読める値」を書かせる。**モデルの推論・未確認**として保存し、同じ文書の断片として検索に入れる（ページの位置付き）。
- WebP での保存は Pillow が要る（導入は利用者の了承待ち）。それまでは PNG。レイアウト解析（Docling）も了承待ち。

### 2.6 AI アシスタントとの接続

- 会話の道具に「ナレッジの横断検索」を足す。答えるモデルがクラウドなら機密と AI 利用の許されない規格を除く。

## 3. 採らなかったもの

| 案 | 理由 |
|---|---|
| reranker の上位 k 件だけを入れる（従来） | 特許・規格は請求項と明細書、条項と引用規格が互いに依存し、断片だけでは判断を誤る |
| 常に全文だけを入れる | 長くなるほど精度が落ちる（Context Rot）。費用と待ち時間。入らない量もある |
| LLM による GraphRAG を特許・規格の全文に | 索引の費用が約 1000 倍、推論の誤りが関係に混ざる。確定した書誌の関係は規則で取れる |
| 画像のまま探す（ColPali 系）を主に | 保存が大きく、優位が確かでない。日本語の工学資料での実績が見当たらない |

## 4. 評価のしかた

- 特許: 請求項の構成要件ごとに、正しい文献・箇所が上位の文書に入るか（文書単位の Recall@k）。
- 規格: 規格番号・条項番号の問いで正しい条項が出るか、旧版を現行と取り違えないか。
- 回答: 出典の無い主張の割合、全文と骨格の切り替えが正しく動くか。

## 出典

- [Retrieval Augmented Generation or Long-Context LLMs?](https://arxiv.org/abs/2407.16833) / [EMNLP Industry 2024](https://aclanthology.org/2024.emnlp-industry.66)
- [LongRAG](https://arxiv.org/html/2406.15319v3) / [In Defense of RAG in the Era of Long-Context LMs](https://arxiv.org/abs/2409.01666)
- [Context Rot（Chroma）](https://research.trychroma.com/context-rot) / [Long Context vs. RAG for LLMs](https://arxiv.org/abs/2501.01880)
- [TelcoAI](https://arxiv.org/html/2601.16984v1) / [Telco-oRAG](https://arxiv.org/html/2505.11856v2) / [Chat3GPP](https://arxiv.org/html/2501.13954v1)
- [GraphRAG vs Vector RAG のコスト（LazyGraphRAG 比較）](https://www.beri.net/article/graphrag-vs-vector-rag-indexing-query-cost-lightrag-lazygraphrag-2026) / [When GraphRAG is overkill](https://hackernoon.com/when-graphrag-is-overkill-a-break-even-test-for-plain-rag-graphs-and-hybrid-search)
- [KLIPA](https://arxiv.org/html/2509.07860v1) / [MemGraph](https://arxiv.org/html/2504.14845v1) / [LLM-RAG 特許検索](https://arxiv.org/abs/2508.14064)
- [JAPIO 年報 2025](https://japio.or.jp/00yearbook/files/2025book/25_4_04.pdf) / [JAPIO 年報 2024](https://japio.or.jp/00yearbook/files/2024book/24_4_04.pdf)
- [UniDoc-Bench](https://arxiv.org/html/2510.03663v3) / [生物医学の MM-RAG 比較](https://arxiv.org/html/2512.16802v1) / [REAL-MM-RAG](https://arxiv.org/pdf/2502.12342)
