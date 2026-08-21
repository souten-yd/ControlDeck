# Release bundle optional features

## Decision

ControlDeck's normal installation path for self-hosted add-ons is a generic
`release-bundle` Optional Feature provider. Source checkout and local builds are
developer-only fallbacks and are not invoked by Settings.

The server accepts only a catalog feature ID. Download URLs, repositories,
versions, commands, hashes, and filesystem destinations are never API inputs.
They are derived from the source-controlled trusted catalog and bounded release
metadata.

## Trust and format

Each catalog entry fixes the GitHub owner/repository, release metadata endpoint,
artifact prefix, package manifest name, maximum download and expanded sizes,
and retained rollback version count. Redirects must remain HTTPS and on an
allowlisted host. Release metadata must contain an artifact for the normalized
`linux-x86_64` platform and an adjacent SHA-256 asset.

The tar archive has one top-level directory and no symlinks, hardlinks, devices,
absolute paths, `..` components, setuid/setgid bits, or files outside its root.
Its `control-deck-feature.json` is schema v1 and binds:

- feature ID, version, platform, architecture;
- one relative executable entrypoint;
- one relative Add-on v2 manifest;
- a loopback health URL and bounded smoke arguments.

The package manifest cannot provide environment variables, arbitrary service
names, working directories, shell fragments, or host paths. The provider builds
the systemd unit itself and supplies the managed version directory and fixed
ControlDeck integration environment.

## Transaction

Install/update uses this order:

1. fetch bounded release metadata and choose the catalog-bound platform asset;
2. download to `features/<id>/downloads/*.partial` and verify SHA-256;
3. safely extract into a sibling staging directory and validate both manifests;
4. run the entrypoint's bounded `smoke` operation;
5. atomically rename staging to `versions/<version>`;
6. atomically replace `current` with a relative symlink to that version;
7. generate and start a ControlDeck-owned user service, then poll loopback health;
8. install/update the Add-on v2 manifest in the Host registry.

Before step 6, failure leaves `current` unchanged. After step 6, service or
health failure switches `current` back to the previous target, restores the old
unit/Add-on manifest, and restarts it. A new version remains side-by-side only
when it passed package validation and smoke; it is not selected after rollback.
Successful updates retain the previous version for rollback and prune only
older provider-managed versions beyond the catalog retention count.

## Removal and ownership

Removal stops and deletes only the provider-owned service, disables/uninstalls
the registered Add-on, removes `current`, then removes the provider-owned
`versions` and `downloads` trees. It does not remove Media Forge data, assets,
models, shared caches, or any directory outside
`<data_dir>/features/<id>`.

## Rejected alternatives

- Git clone/source build: mutable toolchains, excessive disk use, and
  non-reproducible install latency make it unsuitable as the default path.
- Caller-provided URL/repository/command: turns a Settings API into a remote code
  execution primitive and makes catalog review meaningless.
- In-place update: a failed extraction or health check destroys the known-good
  install and cannot provide atomic rollback.
- SHA supplied only by release metadata: compromise of that metadata can replace
  both artifact and hash. The first production catalog revision must pin a
  release signing identity or immutable digest in the catalog before the feature
  is marked generally available; until then it remains an explicit preview.

