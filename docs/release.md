# Release artifact process

The release builder produces a deterministic, offline-installable macOS archive
from one clean Git commit. Repeated builds are byte-identical under the same
recorded Python, pip, setuptools, and wheel toolchain. It never tags, pushes,
creates a GitHub Release, or submits to Devpost.

This repository is the new Messaging v2 release line. The public,
MIT-licensed cmux-centric predecessor remains unchanged at
[`hyyu189/h2o`](https://github.com/hyyu189/h2o), commit
`50683056c896bdb1ae2f74f6ac0740106b43bd36`; that repository has no tag or
GitHub Release as of this audit. It is not overwritten or republished as this
artifact.

## Locked inputs

The builder accepts source only from `git archive HEAD`. It refuses a dirty
worktree, builds the Roundtable wheel as `py3-none-any`, and includes the eight
official PyYAML 6.0.3 wheels for:

- CPython 3.11, 3.12, 3.13, and 3.14;
- macOS arm64 and x86_64.

Each dependency filename, official `files.pythonhosted.org` URL, and SHA-256
digest is pinned in `scripts/build_release.py`. Downloads and caller-provided
offline wheels are verified against those digests.

The archive does not bundle a Python interpreter. Installation requires an
existing CPython 3.11 through 3.14 and fails before writing anything if that
prerequisite is absent or unsupported. This is an explicit candidate support
boundary, not a claim that stock macOS alone is sufficient.

CI uses the exact package versions in `requirements-release.txt`. The builder
records the active Python, pip, setuptools, and wheel versions in
`BUILD-METADATA.json`; it does not claim byte identity across different
toolchains.

## Build

From a clean committed checkout:

```bash
mamba run -n general python scripts/build_release.py
```

To avoid network access, pre-populate a directory with the eight locked wheels:

```bash
mamba run -n general python scripts/build_release.py \
  --dependency-wheel-dir /path/to/locked-wheels
```

The output directory contains:

- `roundtable-messaging-<version>-macos.tar.gz`;
- `SHA256SUMS` for that archive.

The archive has its own `SHA256SUMS` covering every payload file and
`BUILD-METADATA.json` recording the version, exact source commit, source epoch,
project wheel digest, and dependency matrix. It also includes `PROVENANCE.md`,
`CREDITS.md`, the Apache license, `NOTICE` with the applicable predecessor MIT
notice, the compatibility matrix, and the source-commit ledger. Top-level
`install`, `uninstall`, and `migrate` launchers run directly from the extracted
artifact.

## Verify and smoke

```bash
cd artifacts && shasum -a 256 --check SHA256SUMS
cd ..
tar -xzf artifacts/roundtable-messaging-0.1.0-macos.tar.gz
cd roundtable-messaging-0.1.0
shasum -a 256 --check SHA256SUMS
mkdir -p /tmp/roundtable-release-home
./migrate --home /tmp/roundtable-release-home \
  --prefix /tmp/roundtable-release-smoke \
  --link-dir /tmp/roundtable-release-smoke-bin
./install --prefix /tmp/roundtable-release-smoke \
  --link-dir /tmp/roundtable-release-smoke-bin
/tmp/roundtable-release-smoke-bin/roundtable-setup \
  --home /tmp/roundtable-release-home \
  --prefix /tmp/roundtable-release-smoke \
  --harness claude \
  --harness hermes
/tmp/roundtable-release-smoke-bin/roundtable-setup apply \
  --home /tmp/roundtable-release-home \
  --prefix /tmp/roundtable-release-smoke \
  --harness claude \
  --harness hermes
/tmp/roundtable-release-smoke-bin/roundtable-setup status \
  --home /tmp/roundtable-release-home \
  --prefix /tmp/roundtable-release-smoke \
  --harness claude \
  --harness hermes
/tmp/roundtable-release-smoke-bin/roundtable-smoke
/tmp/roundtable-release-smoke-bin/roundtable-setup remove \
  --home /tmp/roundtable-release-home \
  --prefix /tmp/roundtable-release-smoke \
  --harness claude \
  --harness hermes
./uninstall --prefix /tmp/roundtable-release-smoke
```

The migration invocation is a read-only `not-found` plan against the clean
home. The first setup invocation has no subcommand and is likewise a read-only
plan. This manual example omits Codex because plist generation requires an
executable Codex installation; the CI exercise supplies a harmless fake
executable, and the promotion gate uses the real validated CLI.

The GitHub `release-artifact` workflow runs the full tests and safety gate,
builds the same archive, verifies both checksum layers, installs the extracted
payload into an isolated HOME and prefix, and selects all three harnesses using
harmless fake executables. It proves that:

- the default setup plan creates no manifest, config, runtime directory,
  harness skill link, plugin link, or plist;
- `apply`, `status`, and `remove --unload-codex` succeed from the installed
  artifact;
- removal leaves no owned harness config, links, or plist files;
- plan, apply, and status never invoke `launchctl`; explicit teardown queries a
  fake `launchctl` that reports both owned labels not loaded and never reaches
  its bootout sentinel;
- the installed core smoke and package uninstall still pass afterward.

Focused tests also exercise recognized pre-manifest migration plan, apply,
idempotence, rollback, service-loaded refusal, and fail-closed foreign or
modified paths. The workflow uploads a 14-day candidate artifact and
deliberately does not publish a GitHub Release. Configuration automation is not
a substitute for a live cutover or credentialed real-harness E2E.

## Judge journey

The intended five-minute path begins with the archive:

```bash
tar -xzf roundtable-messaging-<version>-macos.tar.gz
cd roundtable-messaging-<version>
./install
export PATH="$HOME/.local/bin:$PATH"  # once per shell; persist it in your shell profile
roundtable
```

`roundtable` is the normal entry: select or create a project folder, select a
harness, review any missing one-time integration, and launch. `roundtable
setup` is always preview-only; `roundtable setup apply` is the explicit expert
path. On first Codex use, the judge may need to review the user hook once with
`/hooks`. Subsequent SessionStart binding is intended to be automatic, with
manual `rt-codex-wake bind` retained only as a fallback.

For a recognized pre-manifest installation, the journey begins before
`./install`:

```bash
./migrate
./migrate apply
./install
```

The user must first stop the two recognized legacy Codex jobs from outside
Codex; migration only queries those labels with read-only `launchctl print` and
never controls their state. If `apply` reports those exact labels as loaded,
the operator can explicitly run
`launchctl bootout gui/$UID/com.roundtable.codex-wake` and then
`launchctl bootout gui/$UID/com.roundtable.codex-app-server` from the ordinary
terminal before retrying. If installation has not yet replaced the migrated
leaves, `./migrate rollback` restores their recorded bytes, modes, and link
targets.

## Promotion gates

Before tagging or attaching the archive to a public release:

1. all CI matrix jobs and the artifact workflow pass;
2. the extracted archive passes install, terminal-baseline smoke, and
   uninstall on a clean macOS account, including the
   `plan -> apply -> status -> remove --unload-codex` setup cycle;
3. a disposable recognized legacy layout passes artifact
   `plan -> apply -> rollback`, then the real development installation passes a
   coordinated migration and cutover without losing registry or mailbox state;
4. clean-account Claude and Hermes setup passes skill discovery, lifecycle
   hook, tripwire, and real send-to-wake-to-drain/ack acceptance;
5. npm Codex `0.144.6` passes the coordinated default-daemon reload, verifies
   that trusted SessionStart `session_id` and injected Roundtable environment
   match the app-server thread and launcher lease, and completes real
   send-to-wake-to-drain/ack acceptance;
6. standalone Codex passes that same acceptance before support is claimed;
7. the same harness acceptance passes in Terminal.app, iTerm2, and Ghostty;
8. the five-minute judge path creates or adopts a non-Git directory, launches
   a project-anchored harness, and completes one visible message round trip;
9. `README.md`, `docs/compatibility.md`, provenance, and Devpost copy describe
   only the gates that actually passed.

At this release-candidate stage, the live development-machine cutover and
Codex hook identity spike have not yet run. They must not be presented in a
video, README support table, or Devpost submission as completed evidence.
