# Strata R9700 acceptance — 2026-11-12

Terminal 0.2.13.1 / HIP engine 0.1.59, Radeon AI PRO R9700 32 GiB,
32 GiB system RAM. Local Flash-Next GSQ-RCO IQ2_XS split GGUF files in
`/data1tb/LLM` remain intact. Generated packs and managed runtime are separate.

## Performance

Single request, thinking off, 256 output tokens, KV int8, MTP auto (spec 4).
Short cases: mean of two runs; uncached prompts, cache_n=0. These synthetic
measurements do not establish response quality or concurrent throughput.

| Context | Input tokens | Prefill tok/s | Decode tok/s | TTFT s |
|---|---:|---:|---:|---:|
| 8K | ~5,800 | 1842.5 | 125.2 | 3.18 |
| 16K | ~5,800 | 2240.4 | 131.6 | 2.62 |
| 32K | ~5,800 | 2263.7 | 130.2 | 2.59 |
| 64K | ~5,800 | 2224.5 | 122.7 | 2.64 |
| 32K | 23,014 | 2619.1 | 111.1 | 8.82 |
| 64K | 23,014 | 2514.1 | 118.0 | 9.19 |
| 64K | 50,911 | 2600.1 | 108.0 | 19.65 |

Use **32K** as this machine's normal default; choose **64K** for longer documents
or code. Available memory and concurrent workloads still govern admission.
Models must be stopped before editing model path, context, KV format, vision,
parallelism or drafting. First activation requires real API readiness.

## Runtime acceptance

- Managed install, update and isolated uninstall completed; model sentinel retained.
- Authenticated shared gateway models and chat returned 200 with actual Strata output.
- PatentBuddy Add-on text completion and streaming returned 200 with before/after
  host-process and model-file provenance; missing credentials / wrong scope rejected.
- OpenCode v2 read a project fixture and wrote the correct result using Strata.
  Fixed inherited PWD so systemd execution uses the selected workspace.
- Desktop 1280px and mobile 320px registration/edit/save UI passed, without
  horizontal overflow or page errors. Temporary registrations removed.
- Evidence: `/data1tb/ControlDeck/strata-pr355-evidence/` on the acceptance host.

Maya-S acceptance is separate: user requests build/install and configuration only;
no Maya model downloads, model loads, inference or throughput acceptance.
