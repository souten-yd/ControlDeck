# Native Knowledge FTS evaluation (draft)

Source: [JSQuAD v1.1 validation](https://github.com/yahoojapan/JGLUE/blob/v1.1.0/datasets/jsquad-v1.1/valid-v1.1.json), blob bcde414e0b7131607bb1fa2c4542dd888f68771a. Selected first eligible 100–240 character paragraph with first two valid questions from each of first 20 eligible articles (20 paragraphs, 40 questions). Origin paragraph is weak gold; small easy corpus, not independent full-corpus benchmark. No source text persisted. Source selection checked by FNV-1a eec8c245 on compact UTF-8 JSON rows transferred into Python. Actual main and #649 textproc.py blob 19442e8b667e550f993a997945009cbf54494cd3, store.py blob fa13387543c08d63ac9b1857a03d7f9edf2eac3d. textproc.py exact blob verified locally; extracted _fts_ranks function UTF-8 FNV-1a 1f2b04ff (without trailing newline) matches. Executed Python copies on SQLite 3.46.1 with equivalent minimal in-memory FTS schema; not full package/API/DB hydration.

|Method|Hit@1|Hit@10|MRR@10|nDCG@10|Empty|p50/p95 ms (single pass)|
|---|---:|---:|---:|---:|---:|---:|
|strict|1/40|1/40|.025|.025|39|.026/.085|
|loose|39/40|40/40|.98125|.98577|0|.063/.179|
|native limit 20|39/40|40/40|.98125|.98577|0|.091/.199|
|native limit 160|39/40|40/40|.98125|.98577|0|.083/.218|
|news literal approximation|0/40|0/40|0|0|40|.010/.031|

News comparator reproduces literal matching against passage body, not SQLAlchemy News route or actual title/summary/source schema. Three explicit negative queries on real corpus returned one forbidden passage each under native fallback; isolated guarded candidate skips fallback for explicit syntax and returned no forbidden passages (all three returned empty). Candidate preserved 39/40 Hit@1, 40/40 Hit@10 on sample, not validated on full corpus or explicit mixed positive/negative cases. It trades recall for strict explicit syntax; must evaluate positive OR/NOT/quoted tests and protected collection boundaries before production adoption. Existing defaults/runtime untouched.

Reproduce in installed backend: python -m tools.eval_native_fts /path/to/valid-v1.1.json --sample ; omit --sample for full corpus. New wrapper not executed in full package; source download DNS blocked in container, copied modules/functions and sample evaluated. No claims about complete API, vector/RRF, confidentiality or full pytest. Next run entire 4,442 queries on native FTS and compare explicit operators, then existing API with isolated test DB. No merge.


## Additional explicit syntax controls

Six manually constructed queries over the same real sample, independently checked against the actual passage text. Native exact result sets 1/6; guarded exact sets 6/6. Both retained valid matches on the two NOT controls; the native fallback added forbidden or unrelated passages. Quoted phrase, quoted phrase + NOT, and quoted OR controls also showed extra native results; plain OR control matched both. These are small regression controls, not a representative syntax benchmark. Executed on the same extracted Python function and SQLite; the CLI wrapper records the expected sets only for this exact sample. No runtime change.
