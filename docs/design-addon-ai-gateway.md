# Add-on AI Gateway — generic host bridge

Status: implementation slice under review  
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

## Critical decisions

1. Do not expose the existing gateway API key to add-ons. Add-on Runtime service tokens and `ai.inference` are the authorization boundary.
2. Do not let add-ons request arbitrary model aliases. That would move routing policy back into every add-on.
3. Do not treat vision as ordinary text `auto`; a loaded text-only model must never be selected for `vision.analyze`.
4. Do not proxy arbitrary image URLs. Add-ons preprocess and send bounded image bytes.
5. Do not make semantic AI mandatory for an add-on to stay healthy. Add-ons should degrade the specific capability when no suitable target is configured.

## Acceptance

Before merge, run the normal backend test gate plus `backend/tests/test_addon_runtime_ai.py`. Real-machine acceptance should cover one text request and one vision request through an enabled fake/test add-on service token, confirm audit records, confirm the selected runtime can be changed without changing the add-on request, and confirm no raw provider/model identity is returned to the add-on.
