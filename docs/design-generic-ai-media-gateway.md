# Generic AI / Media Gateway

Status: normative design for generic ControlDeck host primitives  
Date: 2026-08-25

## 1. Executive decision

ControlDeck already contains the pieces of a common AI/media control plane used by MediaForge and other Add-on v2 services:

- scoped Add-on runtime authentication
- durable Host Jobs and cancel/progress control
- Resource Broker queue/admission/lease/residency
- provider-neutral Host AI routing (`text.generate`, `vision.analyze`)
- explicit AI release
- scoped file/project grants and output commit
- Agent MCP projection for OpenCode and other coding agents
- workflow remote-executor projection
- sandboxed embedded HTTP/WebSocket relay

These facilities are the **Generic AI / Media Gateway**.

This is primarily a contract/consolidation decision. Existing working URLs remain supported; the project must not rename every endpoint merely to create a new namespace.

A small discovery surface is added at:

```text
GET /api/v1/addon-runtime/{addon_id}/gateway/capabilities
```

so an Add-on can detect the Host control-plane features available to its current scoped service identity.

## 2. Why this is generic

MediaForge already uses the same Host primitives for image/video/3D generation that SonicForge needs for TTS/ASR/SFX/music:

```text
MediaForge                           SonicForge
----------                           ----------
image/video/3D worker                TTS/ASR/SFX/music worker
Media asset/provenance               Audio asset/provenance
Media capability router              Audio capability router
          \                           /
           \                         /
            ControlDeck generic Host
            ------------------------
            auth / capability scope
            Host Job
            Resource Broker
            AI router
            grants / output commit
            Agent MCP / Workflow
            HTTP / WebSocket relay
```

The Host boundary test is:

> Would this primitive still make sense for MediaForge, SonicForge, Blender/CAD, archive processing, or another unrelated Add-on?

If yes, it may belong in ControlDeck. If it only makes sense for FLUX, Whisper, Qwen, ACE-Step, Stable Audio, sprites, BGM, voices, or another domain-specific concept, it belongs in the Add-on.

## 3. Ownership boundary

### 3.1 ControlDeck owns

- Add-on lifecycle and manifest/capability authorization
- user/agent/runtime identity and short-lived credentials
- Host Jobs, progress, cancellation and audit
- GPU/device admission and cross-application scheduling
- residency/yield policy and measured resource telemetry
- provider-neutral Host text/vision inference
- explicit Host AI release
- scoped project/file grants and commit receipts
- Agent MCP and Workflow projection
- embedded HTTP/WebSocket relay
- future generic paired-device session authorization/relay

### 3.2 Add-ons own

MediaForge owns:

- image/video/3D model adapters
- media-specific prompt/brief/routing logic
- media assets and media provenance
- image/video/3D validators and project profiles

SonicForge owns:

- TTS/ASR/SFX/music model adapters
- voice profiles, pronunciation, localization and audio routing
- audio/music assets and domain provenance
- live audio pipeline semantics
- M5 audio behavior above the generic device-transport layer

ControlDeck must not gain feature branches such as:

```text
if addon_id == "media-forge": ...
if addon_id == "sonic-forge": ...
if task == "qwen3-tts": ...
if model == "flux": ...
```

## 4. Gateway discovery v1

The discovery response is advisory capability metadata for an already authenticated Add-on runtime identity. It does not grant new authority.

Conceptual response:

```json
{
  "protocol_version": "1.0",
  "addon_id": "sonic-forge",
  "control_plane": {
    "jobs": {"read": true, "write": true, "durable": true},
    "resources": {"acquire": true, "queue": true, "leases": true},
    "files": {"pick": true, "export": true, "scoped_grants": true},
    "ai": {
      "inference": true,
      "release": true,
      "capabilities": {
        "text.generate": true,
        "vision.analyze": true
      }
    }
  },
  "transports": {
    "runtime_http": {"available": true},
    "embedded_http_proxy": {"available": true},
    "embedded_websocket_proxy": {"available": true},
    "device_session": {"available": false}
  }
}
```

Rules:

1. discovery never reports a Host capability as usable when it is not granted to the current Add-on;
2. AI availability is the intersection of `ai.inference` grant and current Host provider availability;
3. discovery must not reveal provider/model names to ordinary Add-on callers;
4. future fields are additive; protocol-breaking changes require a new version;
5. existing dedicated endpoints remain authoritative for execution.

## 5. Resource model for composed pipelines

A composed AI/media pipeline must **not hold one GPU lease across unrelated stages**.

Bad:

```text
acquire ASR/GPU
  -> ASR
  -> call Host LLM while still holding ASR lease
  -> TTS
release
```

This can deadlock or cause avoidable GPU contention because the Host LLM needs its own Broker admission.

Required:

```text
ASR stage
  acquire SonicForge ASR lease
  execute ASR
  release SonicForge ASR lease

LLM stage
  call ControlDeck Host AI router
  Host owns its own admission and provider lifecycle
  request ai.release when the consumer turn is complete

TTS stage
  acquire SonicForge TTS lease
  execute TTS
  release SonicForge TTS lease
```

The same rule applies to MediaForge Director/VLM/generation sequences.

This is **stage-local resource admission**.

## 6. Typed media pipeline relationship

ControlDeck does not need to understand a SonicForge graph such as:

```text
audio -> ASR -> LLM -> TTS -> audio
text  -> LLM -> TTS -> audio
text  -> SFX -> audio
text  -> music -> audio
```

SonicForge owns those typed domain stages.

ControlDeck contributes only generic external stages/primitives:

```text
HostAI(text -> text/json)
HostJob
ResourceLease
InputGrant
OutputGrant
Agent/Workflow invocation
Transport relay
```

This keeps the stable Host contract small while allowing richer Add-on-local orchestration.

## 7. OpenCode and agent use

OpenCode already obtains Add-on agent tools through ControlDeck Agent MCP. This remains the preferred path.

The Host should not add model-specific tools such as `generate_bgm_with_acestep`. Instead:

```text
OpenCode
  -> ControlDeck Agent MCP
  -> Add-on generic/domain tool contribution
  -> durable Add-on/Host Job
  -> Add-on worker
  -> optional ControlDeck project output grant
  -> receipt
```

Examples:

```text
MediaForge: media.generate / media.pack
SonicForge: sonic.generate / sonic.pipeline / sonic.pack
```

The exact SonicForge tool schema belongs to SonicForge.

## 8. Transport strategy

### 8.1 Durable assets and batch jobs

Use ordinary bounded HTTP plus Host Jobs/grants for:

- images
- BGM
- SFX
- voice lines
- long-form transcription
- localization batches
- project asset packs

### 8.2 Embedded browser live events

Use existing Add-on-frame WebSocket proxy for:

- job events
- UI live status
- interactive audio sessions when bounded WebSocket framing is sufficient

### 8.3 Device sessions

M5/ESP32/mobile companion devices must not receive ControlDeck user cookies or raw Add-on service tokens, and Add-on loopback services must not simply be exposed to the LAN.

The future Host primitive is a generic **paired Device Session**, not a SonicForge-only LAN endpoint.

Conceptual responsibility:

```text
Device
  -> ControlDeck/Tailscale-reachable Host endpoint
  -> paired device identity + short-lived session
  -> allowed Add-on/session scope
  -> bounded relay
  -> SonicForge live session
```

The generic Host layer owns:

- pairing authorization
- device identity
- revocation
- short-lived session credentials
- rate/byte/session limits
- Add-on/session scope
- generic relay

SonicForge owns:

- PCM/Opus negotiation
- ASR/TTS semantics
- wake/VAD/AEC policy
- turn state
- barge-in behavior
- transcript/audio application messages

A future `device.sessions` Host capability may be introduced only with a versioned contract and tests. It is deliberately not advertised by Gateway v1 until implemented.

## 9. Live audio backpressure requirements

WebSocket provides transport but not application-level backpressure. Any future live media session must specify:

- maximum frame size
- bounded upstream/downstream queue
- sequence number
- sample/media clock
- ack/window or equivalent flow control
- stale-frame policy
- disconnect/reconnect semantics
- idle/session TTL
- per-device byte/rate limits

For M5 voice chat, bounded low-latency audio may drop stale playback/capture frames rather than accumulating seconds of unusable latency.

## 10. PC, mobile and game asset delivery

Delivery is separate from generation.

Recommended routes:

```text
PC browser save            -> authenticated HTTP asset download
OpenCode/project placement -> Host scoped output grant + commit receipt
mobile browser             -> HTTP asset + WebSocket events/live session
game project               -> Add-on export profile + Host project grant
M5 voice agent             -> paired live Device Session (future Host primitive)
```

Game/audio format policy remains SonicForge-owned. ControlDeck only commits validated bytes inside the granted project boundary.

## 11. Compatibility with MediaForge

No MediaForge migration is required merely to adopt this design.

MediaForge's existing use of:

- `/jobs`
- `/resources`
- `/ai/capabilities`
- `/ai/complete`
- `/ai/release`
- grants/outputs

already conforms to this control-plane model.

MediaForge may optionally call Gateway discovery when useful, but its current endpoint usage remains valid.

## 12. Implementation phases

### GATE-0 — discovery

- add versioned gateway discovery endpoint
- test capability projection
- document ownership and compatibility

### GATE-1 — shared conformance fixtures

Create reusable Host/Add-on contract tests for:

- service token scope
- Host Jobs
- cancellation
- Resource Broker lease lifecycle
- AI complete/release ordering
- grants/output commit
- reconnect/credential refresh

MediaForge and SonicForge should both be able to run the same generic fixture with only Add-on ID/config differences.

### GATE-2 — typed execution metadata

Add generic optional metadata only if proven useful across domains:

- operation/correlation identifiers
- bounded stage labels
- resource stage telemetry
- output receipts

Do not move domain pipeline schemas into ControlDeck.

### GATE-3 — generic Device Session

Only after SonicForge M5/voice-chat requirements are validated:

- pairing
- device registry/revocation
- short-lived session token
- Add-on target scope
- bounded HTTP/WebSocket relay
- audit/rate limits

### GATE-4 — optional live-media transport improvements

Evaluate only if WebSocket is insufficient:

- negotiated Opus
- WebRTC for full-duplex/low-latency media
- relay semantics compatible with authenticated ControlDeck/Tailscale deployment

## 13. Rejected approaches

- moving FLUX/Qwen/Whisper/ACE-Step/Stable Audio adapters into ControlDeck;
- exposing arbitrary Add-on worker endpoints directly through a privileged Host gateway;
- one giant GPU lease for a multi-provider pipeline;
- SonicForge-specific LAN/WebSocket authentication in ControlDeck;
- handing user cookies/service tokens to M5 devices;
- renaming mature working endpoint families solely for cosmetic gateway branding;
- treating device transport availability as permission to execute any Add-on tool.

## 14. Acceptance criteria

The Generic AI / Media Gateway direction is successful when:

1. MediaForge and SonicForge use the same generic Host primitives;
2. domain engines remain replaceable Add-on internals;
3. OpenCode can invoke both through the same Agent MCP projection;
4. GPU contention is controlled by the common Broker with stage-local leases;
5. Host AI calls reuse the common provider router and explicit release behavior;
6. files remain scoped through grants and commit receipts;
7. M5/mobile live access can be added without exposing loopback services or Host credentials;
8. disabling/uninstalling one Add-on does not invalidate the generic Host control plane.
