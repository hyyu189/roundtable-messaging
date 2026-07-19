# Installation and ownership

Roundtable installs into a versioned private Python environment and exposes
stable user-level commands. The installer owns only paths recorded in its
manifest and stops before overwriting an unrelated or locally modified path.

## Current status

The source-install path has passed clean-home installation, repeated-install,
conflict, command, and uninstall tests. A deterministic offline artifact builder
is available, but its output remains a release candidate until the extracted
archive, harness onboarding, and real wake matrix pass the promotion gates.

Do not run the default command over an active pre-manifest `~/.roundtable`
installation. Preview it in isolated paths instead:

```bash
mamba run -n general ./scripts/install.sh \
  --prefix /tmp/roundtable-preview \
  --link-dir /tmp/roundtable-preview-bin
```

## Layout

The default install creates:

- `~/.roundtable/versions/<version>`: the private virtual environment;
- `~/.roundtable/current`: the active version symlink;
- `~/.roundtable/bin`: stable command wrappers;
- `~/.roundtable/install-manifest.json`: owned paths and digests;
- `~/.local/bin/rt-*`: user-visible links to the stable wrappers.

Project registries, runtime state, and project-local `.roundtable` mailboxes are
state, not versioned program files.

## Source install

Source installation requires CPython 3.11 through 3.14 with PyYAML, setuptools
77 or newer, and wheel available to the bootstrap interpreter. On the
development machine:

```bash
mamba run -n general ./scripts/install.sh
```

The source fallback builds a local project wheel without network access and
creates its private environment with access to the bootstrap interpreter's
PyYAML. This mode is for development and verification.

Verify the installed maildir core in an isolated HOME and PATH:

```bash
roundtable-smoke
```

The command exercises the common terminal baseline—send, inbox, quiet
acknowledgement, and drain—without touching the real registry, projects, or
daemon. Its isolated test environment loads no optional terminal adapter.

This core smoke does not configure Claude or Hermes hooks, install the Codex
wake services, or bind a Codex thread. See
[Architecture and adapter boundaries](architecture.md) for the remaining
harness-onboarding gates.

## Offline release install

A release archive will include a `wheels/` directory containing the Roundtable
wheel and compatible PyYAML wheels. From the unpacked archive:

```bash
./scripts/install.sh --wheel-dir ./wheels
```

Release mode uses `--no-index --only-binary` and does not download
dependencies. If an unpacked archive has a top-level `wheels/` directory,
`install.sh` selects it automatically.

See [Release artifact process](release.md) for locked inputs, deterministic
archive generation, checksums, and promotion gates.

## Upgrade gate

Installing a new version atomically advances `~/.roundtable/current`; stable
wrappers and owned LaunchAgent definitions use that path. A running Codex
app-server does not change executable in place. After coordinating a safe
session restart, reload both owned services and run diagnostics:

```bash
rt-codex-daemon install --reload
rt-codex-wake install --reload
rt-doctor
```

Do not perform that reload from the Codex thread whose app-server may be
restarted.

## Uninstall

Use either the installed command or the unpacked release helper:

```bash
roundtable-uninstall
./scripts/uninstall.sh
```

The uninstaller verifies manifest ownership and digests before removal,
bootouts only LaunchAgents whose plist points inside the managed prefix, and
preserves:

- `~/.roundtable/projects.yaml` and its lock;
- global runtime state under `~/.roundtable/.runtime`;
- every project-local `.roundtable` mailbox and ledger.

`--purge-runtime` additionally removes the global ephemeral runtime directory.
It does not remove the registry or project data.
