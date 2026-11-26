# Explicit operator repair (Draft)
Stacked on #653; only knowledge lexical retrieval behavior changes. Explicit quote, standalone OR or leading minus selects strict lexical retrieval; no loose OR, vector-only/hybrid candidate injection, identifier injection or related-document expansion. Normal natural-language mode unchanged. This deliberately trades semantic recall for operator fidelity. Existing parser precedence retained. Scope is chunk-level FTS, not document-wide exclusion, full-document context, other search APIs or MPKR. Not an authorization mechanism. No UI/manifest/default-mode, database, execution or startup changes.

Executed isolated Python SQLite 3.46.1 on four actual published benchmark tables from llama.cpp README blob 3760072121150eab2c33e79a1a7be8b0fec71b57 and Framework benchmarks blob 8ae3b7dfbff78e89b14b319db43562b7c4f55b15 (copied text rows from preceding audit, IDs batch=1, threads=2, model sweep=3, update steps=4). Actual textproc.py blob c2f7eed3c7bae164b3231777a0ef6780e0fdafa8; extracted _fts_ranks function FNV1a 42e309f0 matches branch. Old/new ranked IDs:

| Query | Old | New |
|---|---|---|
| CPU -CUDA | 2,1 | 2 |
| ROCm -Vulkan | 3,4 | none |
| "Qwen2.5-7B" -Vulkan | 3,2,1,4 | none |
| CPU OR Vulkan | 2,3,4 | 2,3,4 |
| "llama 7B" -CPU | 1,2,4 | 1 |
| -CUDA | 1 | none |
| CPUスレッド数 | 2 | 2 |
| バッチサイズ n_batch 512 | 1 | 1 |

8/8 expected sets on this small sample after fix (old 3/8). New repository regression file also tests collection restriction and explicit modes; full package test not yet executed, direct clone DNS blocked. No full-corpus or end-to-end claims. Before adoption, run full pytest, native JSQuAD corpus, document-level semantics and API/LLM policy tests; do not merge.
