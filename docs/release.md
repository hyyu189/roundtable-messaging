# Release artifact process

The release builder produces a deterministic, offline-installable macOS archive
from one clean Git commit. Repeated builds are byte-identical under the same
recorded Python, pip, setuptools, and wheel toolchain. It never tags, pushes,
creates a GitHub Release, or submits to Devpost.

## Locked inputs

The builder accepts source only from `git archive HEAD`. It refuses a dirty
worktree, builds the Roundtable wheel as `py3-none-any`, and includes the eight
official PyYAML 6.0.3 wheels for:

- CPython 3.11, 3.12, 3.13, and 3.14;
- macOS arm64 and x86_64.

Each dependency filename, official `files.pythonhosted.org` URL, and SHA-256
digest is pinned in `scripts/build_release.py`. Downloads and caller-provided
offline wheels are verified against those digests.

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
`CREDITS.md`, the compatibility matrix, and the source-commit ledger.

## Verify and smoke

```bash
shasum -a 256 --check artifacts/SHA256SUMS
tar -xzf artifacts/roundtable-messaging-0.1.0-macos.tar.gz
cd roundtable-messaging-0.1.0
shasum -a 256 --check SHA256SUMS
./install --prefix /tmp/roundtable-release-smoke \
  --link-dir /tmp/roundtable-release-smoke-bin
/tmp/roundtable-release-smoke-bin/roundtable-smoke
./uninstall --prefix /tmp/roundtable-release-smoke
```

The GitHub `release-artifact` workflow runs the full tests and safety gate,
builds the same archive, verifies both checksum layers, installs the extracted
payload into isolated paths, runs `roundtable-smoke`, uninstalls it, and uploads
a 14-day workflow artifact. It deliberately does not publish a GitHub Release.

## Promotion gates

Before tagging or attaching the archive to a public release:

1. all CI matrix jobs and the artifact workflow pass;
2. the extracted archive passes install, terminal-baseline smoke, and
   uninstall on a clean macOS account;
3. clean-account Claude and Hermes setup passes skill discovery, lifecycle
   hook, tripwire, and real send-to-wake-to-drain/ack acceptance;
4. npm Codex `0.144.6` passes the coordinated default-daemon reload and real
   send-to-wake-to-drain/ack acceptance;
5. standalone Codex passes that same acceptance before support is claimed;
6. the same harness acceptance passes in Terminal.app, iTerm2, and Ghostty;
7. `README.md`, `docs/compatibility.md`, provenance, and Devpost copy describe
   only the gates that actually passed.
