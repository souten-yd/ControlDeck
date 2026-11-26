# Japanese passage retrieval diagnostic (draft)

Source: [JGLUE JSQuAD v1.1 validation](https://github.com/yahoojapan/JGLUE/blob/v1.1.0/datasets/jsquad-v1.1/valid-v1.1.json), Git blob bcde414e0b7131607bb1fa2c4542dd888f68771a. No source text copied. All passages indexed; first ten articles' questions dev, rest test. Only questions with at least one exact answer offset retained. One originating paragraph is the weak gold, not exhaustive relevance judgments. Other equivalent passages may be penalized. Fixed BM25 k1=1.2,b=.75, query unique tokens length >=2; CJK character bigram vs trigram, ASCII compound tokens. Not the production FTS implementation, and not MIRACL. No weights tuned. JS function executed against retrieved blob; Node CLI wrapper not executed due container DNS restriction. Reproduce: node backend/tools/eval_jsquad_retrieval.mjs /path/to/valid-v1.1.json. Runtime/latency not measured. No default, UI, API, network, authorization or execution changes. Next: real SQLite FTS5 with app.knowledge.textproc, production route, MIRACL-ja and technical corpus.

```json
{
  "documents": 1145,
  "questions": 4442,
  "skipped": 0,
  "results": {
    "cjk2": {
      "dev": {
        "count": 1862,
        "hit1": 1653,
        "hit10": 1811,
        "mrr10": 0.9170102126063456,
        "ndcg10": 0.9305373479921425,
        "empty": 0,
        "recall1": 0.8877551020408163,
        "recall10": 0.972610096670247
      },
      "test": {
        "count": 2580,
        "hit1": 2363,
        "hit10": 2514,
        "mrr10": 0.9368555432508925,
        "ndcg10": 0.9460283841959389,
        "empty": 0,
        "recall1": 0.915891472868217,
        "recall10": 0.9744186046511628
      }
    },
    "cjk3": {
      "dev": {
        "count": 1862,
        "hit1": 1589,
        "hit10": 1762,
        "mrr10": 0.8862728675430069,
        "ndcg10": 0.9009099098381957,
        "empty": 0,
        "recall1": 0.8533834586466166,
        "recall10": 0.9462943071965628
      },
      "test": {
        "count": 2580,
        "hit1": 2312,
        "hit10": 2471,
        "mrr10": 0.9183041097575981,
        "ndcg10": 0.927935800322536,
        "empty": 2,
        "recall1": 0.896124031007752,
        "recall10": 0.9577519379844961
      }
    }
  }
}
```
