# W5b 実論文検索評価: SciFact（Draft）

2026-11-26。合成6問とは別に、[SciFact](https://github.com/allenai/scifact) / [BEIR](https://github.com/beir-cellar/beir/wiki/Datasets-available) の第三者関連性ラベルで順位融合を評価した。取得先は [公開BEIR配布ミラー](https://github.com/Jonas-Gustafsson/retrieval-evaluation-thesis/tree/main/data/raw/scifact)。GitHub blobから取得した corpus 5,183論文抄録、query 1,109件、test qrels 300問。元本文・質問文はPRに転載せず、文書ID・正解IDと順位のみ固定。

入力blob:
- corpus.jsonl: `2d94adae7011508e4375377add4ab94f2a9c787e`
- queries.jsonl: `535fd802ec3f9a7f5b99c7cf35c57294b6c255b4`
- qrels/test.tsv: `c62f58c75f10c0cee23483b7269bff2902ae4080`
- 固定rank 300問: `7ae439522d91834071c73fac10835b9486dfba5a`

## 方法
全5,183抄録に対し独立のタイトル、本文、タイトル＋本文 BM25 (k1=1.2,b=.75; ASCII小文字英数字、queryの同語重複を除く; stemmingやstopwordなし)。上位20文書IDを保存。各順位リストから上位10のRecall、MRR、nDCGを計算。クエリID昇順の先頭30件を調整に使い、残り270件は重み選択に使わない。qrelsは順位生成に使用しない。比較用の実験BM25であり、既存ControlDeck検索そのものの計測ではない。

## 結果
|方式|調整30 Recall/MRR/nDCG@10|検証270 Recall/MRR/nDCG@10|全300 Recall/MRR/nDCG@10|
|---|---|---|---|
|title|.550/.415/.437|.518/.379/.407|.521/.383/.410|
|abstract|.857/.690/.715|.762/.606/.639|.771/.615/.647|
|combined|.823/.703/.717|.784/.619/.654|.788/.627/.660|
|RRF k60 等重み|.790/.651/.671|.736/.535/.576|.7415/.547/.586|

実装のGit blob `2238252bf32d5263c83428a8280410a819de3067` に一致する research_fusion.py をPythonで実行し、先頭30問の固定順位・goldで確認。文書IDを辞書順を保存する1対1の3桁base36 IDに置換してコンテナに移し、同じ順位/ラベル/tie-breakを維持した。30問の順位の異なる文書IDは909件。RRFのcombined比nDCG勝ち3/同20/負け7。全300の順位生成と検証270の集計はGitHub取得データをJSで実行、RRF/評価計算はPython実装の同一式を使用。先頭30のJS値はPython値と一致。残り270を実Pythonパッケージで実行する試験は未実施。再現用Python evaluatorとgeneratorを追加。

### 重みを調整すれば改善するか
本文重み1固定、title重み0/.1/.25/.5/.75/1で調整30のnDCGを比較:
.715407/.714937/.689420/.688657/.688657/.671136。
最良はtitle重み0（本文のみ）。残り270のnDCG .639214で等重みRRF .576429より高いが、combined .654209より低い。この結果に従い、新しい重みを本番に採用しない。タイトルと本文は独立した情報源ではなく相関が強く、低品質な順位リストからの加点が有害になり得る。複数独立ソース/観点や別言語の効果は別に測る。検証分割は同一SciFact test内で、意味的に近い質問を別splitに含む可能性がある。外部の未使用データ・独立topic分割で再評価する。

## 再現
`cd backend && python -m tools.eval_scifact_rankings`
既存の research_fusion.reciprocal_rank_fusion / rank_metrics を呼ぶ。全300 fixtureは固定。元データがある場合は `python -m tools.build_scifact_rankings /path/to/scifact /tmp/scifact-ranks.json`（blob一致の確認あり、ネット通信なし、既存fixtureを上書きしない）。generator本体の実行は未確認。Python/JSの浮動小数点・tokenizer差は再生成時に比較する。pytest追加。直接clone DNS失敗のため全体pytest未実施。

## 判断・後続
- #641/#644/#645は比較候補のまま。RRFが常に優位と見なさない。既定検索経路、API、UI、権限、実行能力に変更なし。
- 次は既存FTSとrerankerで同じ300問を計測、別独立ソースを融合、MIRACL-jaの860 dev query（[MIRACL](https://github.com/project-miracl/miracl)）で日本語評価。HFデータ行は今回取得できず、日本語の実評価は未実施。実機p50/p95、RAM、CPU、外部検索回数、根拠照合を測る。資料取得/PDF/Office/特許と横断検索は別の実データ評価を行う。マージしない。
