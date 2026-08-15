# CLAUDE.md — ControlDeck implementation lead

## Active responsibility

You are the implementation lead for the **ControlDeck side** of the managed OpenCode extended stack.

ControlDeck must make these combinations easy for users to install, update, enable/disable, repair and rollback:

- OpenCode only
- OpenCode + ExtendCodeAgent (ECA)
- OpenCode + OMO
- OpenCode + ECA + OMO

ControlDeck owns distribution/setup UX only. OpenCode remains the runtime, ECA owns Project Intelligence, and OMO owns orchestration. Do not add ControlDeck-private APIs to ECA/OMO and do not modify those repositories as part of this task.

## Read first

1. `AGENTS.md`
2. `docs/design-opencode-extended-stack.md`
3. `docs/design-opencode-feature.md`
4. `docs/implementation-status.md`
5. existing `backend/app/features/registry.py`, `backend/app/features/cli.py`, `backend/app/integrations/opencode/`, and `deck.sh`

## Execution policy

- Reuse and extend the existing feature manager; do not build a parallel installer framework.
- Separate component management (`opencode`, `extendcodeagent`, `omo`) from stack profiles.
- Use a ControlDeck-owned compatibility manifest with exact tested version tuples. `latest` is experimental, never implicitly recommended.
- Keep all managed installs in ControlDeck user-space data roots; never overwrite/delete unrelated external installs or the user's global OpenCode config.
- Stage updates transactionally, health-check before activation, preserve the previous known-good tuple, and support repair/rollback.
- Generate an isolated OpenCode runtime config that composes the existing provider/model config with ECA and OMO as ordinary OpenCode plugins.
- ECA is currently installed from a pinned generic release/tag/commit into its own venv, then `adapters/opencode` is built with `npm ci && npm run build`; isolate this behind an installer backend so a future ECA release artifact can replace it without redesign.
- Resolve the supported OMO package/version from the compatibility manifest; do not spread historical package names through the codebase.
- ECA and OMO must be independently enable/disable-able. OMO Team Mode defaults OFF until the ECA-side coexistence gate certifies it.
- Normal UX should offer OpenCode only / recommended extended stack / custom, with installed vs recommended vs latest clearly separated.
- Add deterministic plugin ordering, duplicate-plugin detection, compact combined health smoke, failure attribution, responsive UI and tests.
- Follow `AGENTS.md` security/process rules (`shell=True` prohibited, no secrets in logs, no root execution, path validation, local tests/build, status docs update).

## PR sequence

Prefer small PRs:

1. stack/component/compatibility-manifest + transactional state/rollback foundation;
2. ECA managed installer + config + health;
3. OMO managed installer + config + health;
4. combined coexistence smoke/repair/plugin-order handling;
5. user-facing stack UX.

Before each PR, inspect current code and consolidate with existing abstractions rather than duplicating them. Update `docs/design-opencode-extended-stack.md` and `docs/implementation-status.md` as implementation decisions change.

## Done condition

Do not call the work complete until OpenCode-only remains intact, the recommended tested tuple can be installed from a clean state without manual JSON edits, ECA/OMO can be toggled independently, failed staging leaves the previous stack usable, rollback works, combined health detects missing/duplicate components, desktop/mobile UI works, and local tests/build pass.
