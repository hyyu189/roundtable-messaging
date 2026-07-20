# Installation and ownership

Roundtable installs into a versioned private Python environment and exposes
stable user-level commands. The installer owns only paths recorded in its
manifest and stops before overwriting an unrelated or locally modified path.

## Current status

The source-install path and extracted offline artifact pass automated
clean-home installation, repeated-install, conflict, command, harness-setup,
core-smoke, and uninstall tests. The artifact remains a release candidate until
real credentialed harness wake tests and the terminal UX matrix pass the
promotion gates.

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
- `~/.roundtable/skills/shared/roundtable`: the canonical installed skill link;
- `~/.local/bin/rt-*`: user-visible links to the stable wrappers.

Project registries, runtime state, and project-local `.roundtable` mailboxes are
state, not versioned program files.

Harness onboarding is a second ownership layer. After
`roundtable-setup apply`, it also records:

- `~/.roundtable/harness-setup.json`: exact config fragments, links, and plist
  files owned by onboarding;
- `~/.roundtable/backups/harness-setup/`: private backups of existing config
  files before a managed merge;
- `~/.claude/skills/roundtable`, `~/.hermes/skills/roundtable`, and/or
  `~/.codex/skills/roundtable`: selected harnesses' global discovery links to
  the one canonical installed skill.

Those global links mean a user does not download or copy the skill separately
for each new Roundtable project.

Stable wrappers export one absolute host-local runtime root. Set
`RT_RUNTIME_DIR` to override the default `<prefix>/.runtime`;
`RT_CODEX_RUNTIME_DIR` remains a compatibility alias. If both are present they
must name the same path, otherwise the command fails before launching a
harness. The installer and Codex LaunchAgents create this root with user-only
permissions.

## Source install

Source installation requires CPython 3.11 through 3.14 with PyYAML, setuptools
77 or newer, and wheel available to the bootstrap interpreter. On the
development machine:

```bash
mamba run -n general ./scripts/install.sh
```

The source fallback builds a local project wheel without network access and
creates its private environment with access to the bootstrap interpreter's
PyYAML. Installation verifies that the command scripts and their managed
runtime helpers are both present and records their digests, so a same-version
reinstall cannot silently reuse a missing or locally modified lease helper.
This mode is for development and verification.

Verify the installed maildir core in an isolated HOME and PATH:

```bash
roundtable-smoke
```

The command exercises the common terminal baseline—send, inbox, quiet
acknowledgement, and drain—without touching the real registry, projects, or
daemon. Its isolated test environment loads no optional terminal adapter.

This core smoke deliberately does not use credentials, launch a real harness,
load a macOS service, or bind a Codex thread.

## Host onboarding

Run setup with no subcommand first:

```bash
roundtable-setup
```

That is the same as `roundtable-setup plan`: a read-only preflight that reports
which detected harnesses it would configure. It does not create runtime state,
write configuration, or invoke `launchctl`. `apply` and `status` also never
load or unload a service. Harnesses can be selected explicitly and repeatedly:

```bash
roundtable-setup \
  --harness claude \
  --harness hermes \
  --harness codex
```

After reviewing the plan:

```bash
roundtable-setup apply
roundtable-setup status
```

`apply` completes every collision and ownership check before its first
mutation, preserves pre-existing configuration, records private backups, and
is idempotent. It performs these harness-specific actions:

| Harness | Managed onboarding |
| --- | --- |
| Claude | Merges a SessionStart inbox wake hook and Stop drain gate into `~/.claude/settings.json`; links the global Roundtable skill |
| Hermes | Adds one marked `roundtable` plugin entry to `~/.hermes/config.yaml`; links the packaged plugin and global skill |
| Codex | Writes the app-server and wake-bridge plist files under `~/Library/LaunchAgents`; links the global skill |

Setup never installs Claude, Hermes, or Codex itself and never copies
credentials. It configures only harnesses already detected, unless
`--harness` is supplied explicitly.

Codex is intentionally a two-step operation. `roundtable-setup apply` writes
the plist files and creates the private runtime root, but never calls
`launchctl`; writing a service definition cannot silently restart the Codex
session performing the installation. From a different terminal or after
coordinating a safe restart, load or reload both services and verify them:

```bash
rt-codex-daemon install --reload
rt-codex-wake install --reload
rt-doctor
```

Do not run those reload commands inside the Codex thread whose app-server may
be restarted.

## Project onboarding

The supported project-first entry for an interactive user is:

```bash
roundtable
```

Outside an anchored project, its menu offers registered Roundtable projects,
safe setup of the current or another existing folder, and creation of a new
folder. It then lists every configured Claude, Codex, and Hermes seat and
launches the selection with a fenced identity. It never offers the user's home
directory or the filesystem root as a project.

The scriptable project commands remain available:

```bash
roundtable init --here
roundtable init my-project
roundtable init my-git-project --git

# Equivalent low-level spelling:
roundtable-init --here
roundtable-init my-project
roundtable-init my-git-project --git
```

Initialization creates missing Roundtable files and appends clearly marked
blocks to supported existing orientation files. Repeating it is safe. No Git
repository is created by default; `--git` initializes and makes an initial
commit only when the target is not already inside a Git worktree. Existing
repositories and user-owned documents are preserved.

Once registered, launch from the project or use the interactive selector from
elsewhere:

```bash
roundtable claude
roundtable hermes
roundtable codex

# Low-level aliases:
rt-claude
rt-hermes
rt-codex
```

Claude's installed hooks and the Hermes plugin handle their native inbox wake
lifecycle. A fresh Codex thread still needs one explicit, cwd-verified binding:

```bash
rt-codex-wake bind /absolute/path/to/project
```

The real send-to-wake-to-drain/ack path remains a release promotion gate even
though the configuration cycle is automated and tested.

## Offline release install

A generated release archive includes a `wheels/` directory containing the
Roundtable wheel and compatible PyYAML wheels. From the unpacked archive:

```bash
./install
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

Harness configuration must be removed while the managed commands and canonical
skill still exist. If Codex was configured, its two jobs may have been loaded
after setup. Run teardown from Terminal.app, iTerm2, Ghostty, or another normal
shell outside Codex:

```bash
roundtable-setup status
roundtable-setup remove --unload-codex
roundtable-uninstall
```

The `--unload-codex` path refuses to run when `CODEX_THREAD_ID` says the caller
is inside Codex. It first verifies setup ownership, asks `launchctl` about only
`com.roundtable.codex-app-server` and `com.roundtable.codex-wake`, bootouts
either one only when loaded, and then deletes its managed plist files. A
Claude/Hermes-only setup uses plain `roundtable-setup remove` and never invokes
`launchctl`.

From an unpacked release, `./uninstall` can replace the last command. The
package uninstaller refuses to proceed while
`~/.roundtable/harness-setup.json` exists, which prevents dangling harness
configuration. Setup removal verifies owned fragments for drift, removes only
what setup added, and preserves unrelated user configuration. The package
uninstaller then verifies its own manifest ownership and digests before removal
and preserves:

- `~/.roundtable/projects.yaml` and its lock;
- global runtime state under `~/.roundtable/.runtime`;
- every project-local `.roundtable` mailbox and ledger.

`--purge-runtime` additionally removes the global ephemeral runtime directory.
It does not remove the registry or project data.
