# Project Maya-S optional GPU add-on (PR #359)

This is **not Strata**. Project Maya-S is a 96.5 GB GGUF quantization of GLM-5.3-Flash, served by Project Maya's own HIP engine. It is the upstream default S quant. The 32 GB Radeon AI PRO R9700 (`gfx1201`) is a documented supported HIP GPU; Maya-S has reported R9700 operation. Published performance for this exact R9700 + 32 GB host RAM setup is **not verified**.

## Scope

- Separate optional feature id `maya-s` in Settings > Optional features. It does not become the default LLM runtime.
- Code install, update and uninstall are built around `mw00/project-maya` official GitHub Releases, via a fixed source URL, validated semver tags, and a managed code directory.
- Installation checks out **code only**. It never downloads weights or starts inference, and does not touch Ollama, llama.cpp, Strata, or user model files.
- Model preparation is a **separate** `settings.manage` authenticated job with an explicit confirmation (96.5 GB / at least 110 GB free (including headroom beyond model size)). GPU index 0–15 is bounded; defaults to 0. Fixed model: `glm-maya-s`. Fixed loopback API `127.0.0.1:8099`. First setup uses text-only and 8K context to lower VRAM pressure. The GPU ID must be checked on the target PC; the OS enumeration is not guaranteed to align with PCI bus numbers.
- Preparation uses upstream HIP setup, with `--download-model --no-start`. After preparing, **explicit** Start launches a managed systemd-user unit; Stop ends it. The job tests the loopback health endpoint; an accepted systemd unit alone does not count as a ready model.
- Model files are retained under `data_dir()/models/maya-s` if the managed code is uninstalled. The original system-wide Maya, HOME, and other local runtime settings are never modified.
- The managed Maya model is advertised to ControlDeck's local model and provider catalog **only after explicit HIP preparation**. The shared gateway can forward an explicitly selected Maya alias after the administrator has started it. Dormant Maya is excluded from the default AUTO candidate and never auto-starts/downloading weights. Maya is NOT added to the default runtime selector nor made the default chat engine.
- The Maya model's own OpenAI-compatible API stays bound to loopback. The existing ControlDeck gateway's authenticated endpoint controls remote client access, rather than exposing the Maya port itself. Load/Stop/Delete/Download generic provider capabilities are not granted to Maya: those operations require explicit authenticated feature jobs.

## Compatibility and caveats

- Official upstream references: [AMD Maya](https://github.com/mw00/project-maya/blob/main/docs/AMD_MAYA.md), [Setup](https://github.com/mw00/project-maya/blob/main/docs/SETUP.md), [Maya-S quant](https://github.com/mw00/project-maya/blob/main/bench/results/MAYA-S.md).
- Ubuntu/Linux x86_64, HIP-capable `gfx1201` R9700, ROCm 7+ with HIP compiler, hipBLAS, Python 3.10+, CMake 3.24+, C++20 compiler. The user's ROCm 10 setup has **not** been verified specifically with upstream Project Maya. The manager does not install system packages or elevate privileges.
- Very limited system RAM (e.g. 32GB RAM + 32GB VRAM) will require SSD-backed expert reads for Maya-S. Performance and practical context length must be measured on the actual GPU + storage; do not promise specific tokens per second.
- At the time of implementation, upstream release `v1.1.60` existed (2026-11-12). Installation checks the then-current official `releases/latest` response and keeps the version as a managed marker. The repo tag is fetched explicitly; untrusted arbitrary download URLs are not accepted.
- Never serve raw source files publicly, expose the port beyond loopback, enable start on server boot, or turn on automatic source/update/model download.

## Acceptance tests and open items

- [x] Optional feature catalog and install/update/uninstall hooks
- [x] Explicit model preparation job, independent start/stop, and UI confirmation
- [x] No-network mocked Python tests for no implicit download, disk checks, source origin, systemd unit, and model retention
- [ ] Full pytest suite / frontend build from the actual new PR code: **not run**
- [ ] Real R9700/ROCm10/Maya-S setup/download/load/API/generation/unload test
- [ ] Concurrent jobs, cancellation, and repeated install/update/uninstall smoke test on Linux
- [x] Add Maya to opt-in local provider/model catalog and shared gateway without default/autonomous start
- [ ] Verify the gateway returns a Maya response after explicit real R9700 start
- [ ] Review stacked PR against PR #355 after it merges, change base to main, recheck all code/docs/tests, then Ready for review and a single manual CI run

## Developer test command (do not run via GitHub Actions repeatedly)

```bash
cd backend
../.venv/bin/python -m pytest -q tests/test_maya_s_addon.py tests/test_gpu_runtime_addon.py tests/test_features.py
../.venv/bin/python -m pytest -q tests
cd ../frontend
npm run build
```

The PR head branch retains its historical `feat/maya-s24-optional-addon-20261112` name; actual feature IDs, paths and managed files use Maya-S. No automatic migration or deletion of old Maya-S24 model data is attempted.

This is a **Draft** until tests and R9700 hardware verification complete. Do not merge or release merely because the UI is present.
