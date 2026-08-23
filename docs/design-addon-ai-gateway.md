# Add-on AI Gateway — generic host bridge

Status: implemented and real-host verified
Date: 2026-08-22

## Purpose

Powerful add-ons may need text reasoning or image understanding, but must not bind to Ollama, llama.cpp, a model alias, or a raw provider port. ControlDeck already owns runtime/provider selection, multimodal message normalization, structured-output fallback, cancellation/admission behavior, and GPU supervision. This bridge projects that host-owned AI path to Add-on Runtime credentials.

## Boundary

ControlDeck owns:

- runtime/provider/model selection;
- `text.generate` versus `vision.analyze` target resolution;
- provider-specific payload conversion;
- structured-output fallback;
- runtime lifecycle and GPU/KV supervision;
- add-on authorization/audit.

The add-on owns:

- task-specific prompts and JSON schema;
- bounded image preprocessing before transfer;
- interpretation of the structured result;
- product-specific retries and UI.

No Media Forge name, route, schema, or model appears in this host implementation.

## Contract

Add-on manifests request the generic host capability:

```text
ai.inference
```

Authenticated service tokens may then call:

```text
GET  /api/v1/addon-runtime/{addon_id}/ai/capabilities
POST /api/v1/addon-runtime/{addon_id}/ai/complete
```

`complete` accepts only:

```text
capability = text.generate | vision.analyze
messages[]
response_format?      # JSON object / JSON schema dialect already normalized by runtime_provider
temperature
max_tokens
timeout_seconds
```

There is deliberately no provider, port, runtime, or model field. The response returns content and requested capability, not the selected implementation identity.

Vision inputs use bounded `data:image/*;base64,...` parts only. Remote image URLs are rejected so the bridge cannot become an SSRF proxy. Current bounds are four images, 2 MiB each, 8 MiB aggregate.

## Target resolution

Initial host resolution follows the selected runtime policy:

- llama.cpp: `role=llm`; `vision.analyze` additionally requires a configured `mmproj_path`;
- Ollama: installed chat models; `vision.analyze` additionally requires the existing per-model `vlm_enabled=true` marker.

Loaded targets are preferred, then configured order/default. This provider-specific knowledge remains inside ControlDeck and is not part of the Add-on contract. Future runtimes should extend the resolver/capability catalog, not add provider branches to consumers.

## Model registration and operator visibility

When a new llama.cpp GGUF is registered, ControlDeck resolves the model through
the configured file roots and scans only that resolved model's directory for a
bounded, deterministic list of regular `*mmproj*.gguf` files. Symlinks and
projectors outside that directory are not candidates. Detection makes VISION
available in the registration form but never enables it automatically. The
operator must explicitly turn VISION on, which stores the selected
`mmproj_path`; changing the model path resets the choice to disabled.

The common model list exposes only the boolean VISION capability needed by the
Host UI. A `VISION` mark is shown for llama.cpp instances with `mmproj_path` and
for existing Ollama models with `vlm_enabled=true`; projector paths remain a
llama.cpp registration/configuration detail.

## Critical decisions

1. Do not expose the existing gateway API key to add-ons. Add-on Runtime service tokens and `ai.inference` are the authorization boundary.
2. Do not let add-ons request arbitrary model aliases. That would move routing policy back into every add-on.
3. Do not treat vision as ordinary text `auto`; a loaded text-only model must never be selected for `vision.analyze`.
4. Do not proxy arbitrary image URLs. Add-ons preprocess and send bounded image bytes.
5. Do not make semantic AI mandatory for an add-on to stay healthy. Add-ons should degrade the specific capability when no suitable target is configured.

## Acceptance

Before merge, run the normal backend test gate plus `backend/tests/test_addon_runtime_ai.py`. Real-machine acceptance should cover one text request and one vision request through an enabled fake/test add-on service token, confirm audit records, confirm the selected runtime can be changed without changing the add-on request, and confirm no raw provider/model identity is returned to the add-on.

Completed on 2026-08-22 with the llama.cpp/Vulkan runtime and
`/data1tb/LLM/Qwen3.6-35B-A3B-GGUF/{Qwen3.6-35B-A3B-Q4_K_M.gguf,mmproj-F16.gguf}`.
The generic Add-on Runtime bridge produced an exact text response and correctly
described a 512x512 image. The same canonical VISION request (identical SHA-256)
was then executed through two different Host-selected aliases. Both responses
contained only `content` and `capability`; five broker leases reached
`released`, and five audit records contained only the requested capability.

## 明示解放 `POST /{addon_id}/ai/release`

`ai.inference` を持つ add-on が「自分の AI ターンは終わった」と宣言するための
汎用 primitive。AI の直後に自分の処理で GPU を使う add-on は、モデルの寿命を
ControlDeck が所有している以上、これ以外に意思を伝える手段が無い。

**要求であって命令ではない。** ControlDeck は自分の chat、OpenCode セッション、
他の add-on が共有モデルを使っている間は必ず断り、理由を返す。呼び出し側は
後段で匿名の out-of-memory に当たる代わりに、理由を提示して説明できる。

判定は新設しない。30 分の idle unload が使う判定集合をそのまま再利用し、
時間ではなく要求で同じ決定を起こす。

```text
1. policy      gateway_only かつ yield_max_level >= UNLOAD
2. drain       実行中の gateway 要求が 0 になるまで drain_timeout_sec 待つ
               （chat / OpenCode / 他 add-on の実行中要求を切らない）
3. guards      _has_connected_clients   -> clients_connected
               _opencode_session_uses   -> opencode_active
               idle_exclude             -> idle_excluded
               role != "llm"            -> not_an_llm_instance
                                           （embedding / reranker は対象外）
4. stop        すべて通ったときだけ stop_instance
```

応答は `{"released": bool, "reason": str, "freed_bytes": int}`。

broker の yield が持つ thrash / uptime heuristics は参照しない。あれは
**非自発的な preemption** の往復を防ぐためのもので、自発的な返却は preemption
ではない。使用中判定は変わらず適用する。

対になる変更として `/ai/complete` に `llama.ensure_ready` を追加した。
`/api/v1/llm` の `gateway_chat` と同じ on-demand 起動になり、明示解放や
idle unload で停止した instance へ add-on の要求が飛んでも復帰する。
これが無いと「降ろす」機能は次の要求を壊す。
