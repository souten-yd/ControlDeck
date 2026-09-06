# OpenCode Extended Stack — Managed Installation Design

Status: proposed ControlDeck-side design
Date: 2026-08-16
Scope owner: ControlDeck

## 1. Goal

Make the existing OpenCode feature easy to install and use with optional complementary extensions:

- OpenCode — coding-agent runtime;
- ExtendCodeAgent (ECA) — Project Intelligence / Verification Intelligence;
- Oh-My-OpenAgent (OMO) — orchestration, background agents, optional Team Mode and related tooling.

ControlDeck owns only installation, version selection, generated OpenCode configuration, status,
update/rollback and user-facing UX. It must not create a ControlDeck-specific ECA or OMO API.

The runtime remains ordinary OpenCode with ordinary OpenCode plugins.

## 2. User profiles

Expose simple profiles while retaining advanced/custom control:

1. **OpenCode Core** — OpenCode only.
2. **OpenCode + Intelligence** — OpenCode + ECA.
3. **OpenCode + Orchestration** — OpenCode + OMO.
4. **Recommended Extended Stack** — OpenCode + ECA + OMO, only when the exact version tuple has passed
   coexistence checks.
5. **Custom** — advanced component/version/mode selection.

The current OpenCode-only behavior remains supported and must not regress.

Recommended Extended Stack must never mean "install latest of everything". It means a version tuple
selected by a ControlDeck compatibility manifest and backed by recorded compatibility evidence.

## 3. Compatibility manifest

Maintain a small machine-readable ControlDeck-owned manifest, for example:

```json
{
  "schema": 1,
  "channels": {
    "recommended": {
      "opencode": "<tested-version>",
      "extendcodeagent": "<tested-version>",
      "omo": "<tested-version>",
      "compatibility": "recommended",
      "team_mode": "disabled",
      "tested_at": "<timestamp>"
    }
  }
}
```

The actual package/release coordinates are distribution metadata, not domain constants in unrelated
ControlDeck code.

Compatibility states:

- `unknown`;
- `incompatible`;
- `degraded`;
- `compatible`;
- `recommended`.

A ControlDeck update may refresh the manifest independently of the installed component versions.

## 4. Version policy

### Recommended channel

- install exact tested versions;
- update only to another tested tuple;
- preserve previous tuple for rollback;
- do not silently cross a known incompatible tuple.

### Latest/experimental channel

- opt-in only;
- may resolve current upstream versions;
- immediately runs health/coexistence checks;
- if the combined stack is unverified, label it experimental rather than recommended.

This distinction is especially important for OMO because plugin/package/config naming and OpenCode
integration have changed over time.

## 5. Managed installation boundary

Reuse and generalize the current ControlDeck feature-management principles:

- user-space installation only;
- no `sudo`;
- ControlDeck-managed roots under the data directory;
- do not delete or mutate unrelated external/global installations;
- generated runtime configuration is isolated from the user's normal OpenCode config unless the user
  explicitly chooses to reuse it;
- uninstall removes only ControlDeck-managed files/config references;
- existing external OpenCode remains a valid source when selected.

Do not make the ControlDeck backend depend on OMO/ECA Python/TypeScript internals. Treat each component
as an installable/runtime unit with explicit health checks.

## 6. ECA packaging requirement

ControlDeck must consume a generic ECA release artifact/install method. It must not invent a
ControlDeck-specific ECA protocol.

Until ECA publishes a stable generic installable artifact that includes the OpenCode adapter plus its
sidecar/runtime dependencies, the ECA component may remain `experimental/unavailable` in the managed
bundle UI.

The ControlDeck installer should support the generic release mechanism selected by ECA productization,
not force ECA core changes for ControlDeck.

## 7. OMO installation requirement

Use the currently supported OMO package/plugin identity from the compatibility manifest rather than
hard-coding a historical package name throughout ControlDeck.

ControlDeck should configure OMO as an ordinary OpenCode plugin in its generated OpenCode config.
Where useful, an optional upstream doctor command may be run as an additional diagnostic, but
ControlDeck's own health result must be based on observable OpenCode plugin/agent/tool behavior rather
than only a doctor exit code.

Surface OMO anonymous-telemetry behavior in the UI. For a ControlDeck-managed stack, default to no
telemetry unless the user explicitly enables it.

## 8. Generated OpenCode configuration

The generated runtime config should compose components rather than replace unrelated provider/model
settings.

Conceptually:

```text
base OpenCode config
  + selected provider/model
  + ECA plugin/config when enabled
  + OMO plugin/config when enabled
  + compatibility-approved plugin order when required
  -> isolated ControlDeck runtime config
```

Do not copy user secrets into the compatibility manifest.

If coexistence testing proves plugin order irrelevant, preserve a stable deterministic order anyway.
If order matters, the manifest records the required order and the UI reports the limitation.

## 9. OMO Team Mode

Team Mode is a separate advanced capability and must not be silently enabled merely because OMO is
installed.

Default:

- OMO installed: allowed;
- Team Mode: OFF.

Enable Team Mode in a recommended profile only after the corresponding OpenCode + OMO + ECA
Team/worktree coexistence gate has passed for the selected version tuple.

The user may opt into experimental Team Mode earlier, with a clear compatibility status.

## 10. One-click UX

The OpenCode feature page should evolve from a single runtime status into a compact stack card:

```text
OpenCode                         Healthy   1.x
Project Intelligence (ECA)      Healthy   x.y
Agent Orchestration (OMO)       Healthy   x.y
Compatibility                   Recommended
Team Mode                        Off

[Install recommended stack] [Update] [Repair] [Rollback]
[Customize]
```

First-time flow:

1. detect external/managed OpenCode;
2. show recommended stack and its component versions;
3. user chooses OpenCode-only, recommended stack, or custom;
4. stage installation;
5. generate isolated runtime config;
6. run health/coexistence smoke;
7. atomically activate the new tuple only after checks pass;
8. retain previous working tuple/config for rollback.

Do not require the user to manually edit JSON/JSONC for the normal recommended path.

## 11. Health checks

### OpenCode

- executable resolves;
- version command succeeds;
- minimal run/session smoke succeeds.

### ECA

- plugin loads;
- sidecar starts/reconnects;
- `pi_status` responds;
- selected `pi_*` tool smoke passes;
- off-mode remains inert.

### OMO

- plugin loads;
- expected primary agent/tool registration is visible;
- configured feature gates resolve;
- optional doctor diagnostics are captured when available.

### Combined stack

- both plugins load in the approved order;
- no duplicate/missing tool IDs;
- `pi_*` tools remain available;
- OMO agents/tools remain available;
- a minimal OpenCode agent task runs exactly once;
- no hook/plugin startup error;
- session exit/restart is clean.

Full semantic compatibility belongs to ECA/OMO coexistence validation; ControlDeck performs the compact
installer smoke needed to avoid activating a broken tuple.

## 12. Transaction and rollback

Do not update a live known-good stack in place without a recovery path.

Preferred flow:

```text
current tuple A
  -> install/stage tuple B
  -> generate B config
  -> smoke B
  -> PASS: atomically activate B and retain A metadata
  -> FAIL: leave A active, mark B failed, expose diagnostics
```

Rollback restores both component tuple and generated runtime config.

## 13. Repair

Add a user-facing Repair action that can:

- re-resolve managed component paths;
- reinstall a missing managed package/artifact at the pinned version;
- regenerate isolated OpenCode config;
- rerun component and coexistence health checks;
- preserve projects/session data where owned outside the managed package root.

Repair must not delete an external user installation.

## 14. Updates

Display separately:

- installed version;
- recommended tested version;
- latest upstream version when known;
- compatibility status.

User actions:

- `Update recommended` — safest tested tuple;
- `Try latest` — advanced/experimental;
- `Rollback` — previous known-good tuple.

Do not automatically promote an untested upstream OMO/OpenCode/ECA combination to recommended.

## 15. Acceptance

The managed extended stack is ready for normal users when:

1. OpenCode-only install/update/uninstall still works;
2. one-click recommended install succeeds from clean ControlDeck state;
3. existing external OpenCode is not overwritten or removed;
4. ECA and OMO can be independently enabled/disabled;
5. combined stack health smoke detects missing plugin/tool/agent registration;
6. failed update leaves the previous tuple usable;
7. rollback restores the previous tuple/config;
8. Team Mode remains off by default until separately certified;
9. ordinary usage requires no manual config editing;
10. mobile and desktop UI expose the same component/compatibility state;
11. secrets are not written to compatibility metadata/logs;
12. compatibility status is never inferred merely from "latest" versions.

## 16. Responsibility boundary

ControlDeck owns distribution UX and managed setup.
OpenCode owns the runtime/plugin mechanism.
ECA owns Project Intelligence and ECA/OMO coexistence evidence relevant to ECA behavior.
OMO owns its orchestration behavior.

## 17. Blender Skills execution profile (2026-09-06)

Choose option B: retain the pinned upstream production references, but expose a
ControlDeck-adapted `blender-director` through managed `skills.paths`. Its executable
contract is the existing MediaForge typed scene tools, not BlenderMCP. The upstream
94 skill documents are retained as reference material outside the exposed runtime
skill root; they must not silently add nonexistent MCP calls to the agent context.

The director preserves planning, scale/triangle budgets, staged construction,
reference-image comparison and validation. It maps supported work to the seven
existing recipe operations plus scene/material/export and durable Job tools.
Unsupported work (arbitrary Python, sculpting, rigging, simulations, unsupported
exports) is reported explicitly, not executed through a guessed substitute. Visual
comparison is not claimed from a scene metadata snapshot alone.

Option A would introduce another managed executable, Blender addon, persistent
session and GPU reservation lifecycle alongside MediaForge. This is unnecessary
for the supported typed asset workflow. Option C alone cannot resolve instructions
that call BlenderMCP names and arbitrary bpy; additional typed operations remain
MediaForge-owned, capability-gated follow-up work rather than a compatibility lie.

Installation remains under `<data>/skills/versions`, pinned to upstream commit
`8f778d2405a214b508d4c7d80742be8e43acdd52` plus a versioned local adapter. Preserve the
upstream MIT notice. Stage and validate both upstream and adapter before activation;
failure must retain the previous files and state, including a deliberate disable.
Do not import repository MCP config, install BlenderMCP, touch global agent config,
or modify an existing Blender installation.

Skill runtime prerequisites are declarative catalog metadata. A generic check uses
the registered Add-on's declared tools and bounded public capability response.
Installation and execution readiness are separate: missing/disabled service,
missing Blender batch runtime, missing tools or an old unadapted installation must
be visible in Settings and excluded from generated runtime skill paths. Blender GUI
need not be running; MediaForge owns on-demand batch execution and Broker policy.
Repeat capability discovery in the director before execution, since readiness can
change after a session starts. No authority is conferred by a successful health check.

All blocking setup/check/config generation runs in a sync endpoint/threadpool, not
on the async event loop. No new always-running subprocess is owned by the web worker.

A problem must be fixed in the owning layer rather than creating cross-project private interfaces.
