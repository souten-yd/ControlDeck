# Project Maya-S24 optional GPU add-on (PR #359)

This is **not Strata**. Project Maya-S24 is a 94.7 GB GGUF quantization of GLM-5.3-Flash, served by Project Maya's own HIP engine. It is primarily tuned for <=24 GB GPUs, but may run on the 32 GB Radeon AI PRO R9700. Published performance for the exact R9700 + 32 GB host RAM setup is **not verified**.

## Scope

- Separate optional feature id `maya-s24` in Settings > Optional features. It does not become the default LLM runtime.
- Code install, update and uninstall are built around `mw00/project-maya` official GitHub Releases, via a fixed source URL, validated semver tags, and a managed code directory.
- Installation checks out **code only**. It never downloads weights or starts inference, and does not touch Ollama, llama.cpp, Strata, or user model files.
- Model preparation is a **separate** `settings.manage` authenticated job with an explicit confirmation (94.7 GB / at least 110 GB free). GPU index 0–15 is bounded; defaults to 0. Fixed model: `glm-maya-s24`. Fixed loopback API `127.0.0.1:8099`. First setup uses text-only and 8K context to lower VRAM pressure. The GPU ID must be checked on the target PC; the OS enumeration is not guaranteed to align with PCI bus numbers.
- Preparation uses upstream HIP setup, with `--download-model --no-start`. After preparing, **explicit** Start launches a managed systemd-user unit; Stop ends it. The job tests the loopback health endpoint; an accepted systemd unit alone does not count as a ready model.
- Model files are retained under `data_dir()/models/maya-s24` if the managed code is uninstalled. The original system-wide Maya, HOME, and other local runtime settings are never modified.
- A separate hosted OpenAI-compatible API is provided on the loopback port. **The first PR does not change shared ControlDeck gateway selection semantics or silently adopt Maya as the default**; connecting the hosted API to a shared LLM provider requires a separate explicit design/review, if requested.

## Compatibility and caveats

- Official upstream references: [AMD Maya](https://github.com/mw00/project-maya/blob/main/docs/AMD_MAYA.md), [Setup](https://github.com/mw00/project-maya/blob/main/docs/SETUP.md), [Maya-S24 quant](https://github.com/mw00/project-maya/blob/main/bench/results/MAYA-S24.md).
- Ubuntu/Linux x86_64, HIP-capable `gfx1201` R9700, ROCm 7+ with HIP compiler, hipBLAS, Python 3.10+, CMake 3.24+, C++20 compiler. The user's ROCm 10 setup has **not** been verified specifically with upstream Project Maya. The manager does not install system packages or elevate privileges.
- Very limited system RAM (e.g. 32GB RAM + 32GB VRAM) will require SSD-backed expert reads for Maya-S24. Performance and practical context length must be measured on the actual GPU + storage; do not promise specific tokens per second.
- At the time of implementation, upstream release `v1.1.60` existed (2026-11-12). Installation checks the then-current official `releases/latest` response and keeps the version as a managed marker. The repo tag is fetched explicitly; untrusted arbitrary download URLs are not accepted.
- Never serve raw source files publicly, expose the port beyond loopback, enable start on server boot, or turn on automatic source/update/model download.

## Acceptance tests and open items

- [x] Optional feature catalog and install/update/uninstall hooks
- [x] Explicit model preparation job, independent start/stop, and UI confirmation
- [x] No-network mocked Python tests for no implicit download, disk checks, source origin, systemd unit, and model retention
- [ ] Full pytest suite / frontend build from the actual new PR code: **not run**
- [ ] Real R9700/ROCm10/Maya-S24 setup/download/load/API/generation/unload test
- [ ] Concurrent jobs, cancellation, and repeated install/update/uninstall smoke test on Linux
- [ ] Confirm shared LLM gateway interoperability requirement before expanding scope
- [ ] Review stacked PR against PR #355 after it merges, change base to main, recheck all code/docs/tests, then Ready for review and a single manual CI run

## Developer test command (do not run via GitHub Actions repeatedly)

```bash
cd backend
../.venv/bin/python -m pytest -q tests/test_maya_s24_addon.py tests/test_gpu_runtime_addon.py tests/test_features.py
../.venv/bin/python -m pytest -q tests
cd ../frontend
npm run build
```

This is a **Draft** until tests and R9700 hardware verification complete. Do not merge or release merely because the UI is present.
