# Roundtable Messaging v2

Roundtable is a local coordination layer for coding agents. Messaging v2 uses
durable per-project mailboxes as the delivery fact source and wakes supported
harnesses through native mechanisms instead of injecting keystrokes.

> Build status: the source installer and deterministic offline release archive
> pass automated clean-home install, setup, core smoke, and uninstall
> tests. The result is a release candidate, not yet a public support claim: the
> npm Codex cold start and the corrected launchd-to-socket-peer identity check
> have passed on the development machine. The SessionStart identity spike,
> real credentialed harness wake tests, and the mainstream terminal matrix
> remain promotion gates.

## Why it exists

Multi-agent terminal workflows become fragile when delivery depends on pane
focus, keyboard timing, or a particular terminal multiplexer. Roundtable
separates durable delivery from wake-up:

1. a sender atomically writes a message into the recipient's project mailbox;
2. an optional harness adapter wakes an online recipient;
3. the recipient drains, acknowledges, and archives the message;
4. an offline recipient keeps the message until it returns.

The core has no terminal-emulator dependency. Terminal.app, iTerm2, Ghostty,
and other normal terminal hosts use the same delivery and harness-adapter path.
cmux adds optional workspace/topology features rather than a different class of
transport.

## Build Week scope

The submitted Messaging v2 architecture and deliverable were built during the
2026 OpenAI Build Week submission period, replacing an earlier keyboard-based
prototype. The repository keeps the earlier baseline only where it is required
to make the rewrite reviewable.

The public, MIT-licensed cmux-centric v1 snapshot remains unchanged at
[`hyyu189/h2o`](https://github.com/hyyu189/h2o), commit
`50683056c896bdb1ae2f74f6ac0740106b43bd36`. It is predecessor evidence, not
Build Week output and not the repository being released here.

- [Development and attribution boundary](PROVENANCE.md)
- [Contributor roles](CREDITS.md)
- [Source commit ledger](docs/provenance/source-commits.tsv)
- [Architecture and adapter boundaries](docs/architecture.md)
- [Runtime compatibility and validation](docs/compatibility.md)
- [Release artifact process](docs/release.md)

Ocean directed the product. GPT-5.6 through Codex was the primary implementation
environment. Fable 5 contributed specified early code, documentation,
configuration, design, and review; those contributions are recorded
commit-by-commit rather than described as GPT-5.6-only work.

All productization work begun in this public repository is GPT-5.6/Codex-led.

## Current compatibility status

| Surface | Status |
| --- | --- |
| Installer runtime | Requires an existing CPython 3.11–3.14; the archive bundles all Python package dependencies, not the interpreter |
| Terminal.app, iTerm2, and Ghostty | One first-class terminal baseline; automated core smoke passes, full harness wake UX matrix remains a release gate |
| Claude Code | Owned global skill links plus SessionStart and Stop hooks are packaged and configuration-tested; real clean-account wake E2E remains a release gate |
| Hermes | Owned global skill and plugin links are packaged and configuration-tested; real clean-account wake E2E remains a release gate |
| npm Codex CLI `0.144.6` | Exact-release protocol smoke, live cold start, launchd-to-socket-peer identity, and automated service/auto-bind coverage pass; RC4 upgrade, live hook identity, and full wake E2E remain release gates |
| Codex standalone | Canonical resolver path implemented; not yet claimed as supported because no standalone install has completed the live gate |
| cmux | The same baseline plus optional project/workspace topology, diagnostics, and notifications |
| tmux and cross-host SSH | Not yet supported |

## Release target

The Build Week P0 release is complete only when it provides:

- an idempotent user-level installer and precise uninstaller;
- a five-minute judge path from a packaged release;
- verified support for the current npm Codex and an honestly tested standalone
  path;
- a terminal-emulator-independent end-to-end path across the mainstream
  terminal UX matrix;
- accurate diagnostics, recovery, tests, and public-safety checks.

Same-host tmux support is P1. Cross-host transport, Linux service management,
and multi-auth switching are roadmap items.

## Install the release candidate

The judge and new-user path begins with the release artifact, not a source
checkout or rebuild. It requires an existing CPython 3.11 through 3.14; the
archive is offline for package dependencies but does not bundle CPython. The
installer explains how to select a supported interpreter if `python3` is not
the right one:

```bash
tar -xzf roundtable-messaging-<version>-macos.tar.gz
cd roundtable-messaging-<version>
./install
export PATH="$HOME/.local/bin:$PATH"  # once per shell; persist it in your shell profile
roundtable
```

`roundtable` is the normal entry point. It chooses or creates a project folder
(Git is optional), asks which installed and configured Claude, Codex, or Hermes
seat to launch, and performs any missing one-time harness setup only after
showing the owned changes and receiving confirmation. A Roundtable project may
be any ordinary folder, including a non-code folder.

Roundtable-managed Codex launches require that project anchor. This lets the
launcher publish a fenced host-service lease and auto-bind the native thread
without a reload race. Claude and Hermes may still use the explicit
unanchored option; users who want unanchored Codex can run native `codex`
directly, outside Roundtable messaging.

## Setup and day-to-day use

For normal users, the first `roundtable` launch is the onboarding flow. The
standalone setup commands are review and expert controls:

```bash
roundtable setup          # preview only; never writes configuration
roundtable setup apply    # explicit expert/scriptable apply
roundtable setup status
```

For Codex, setup installs an owned SessionStart hook and two owned macOS
LaunchAgent definitions. On first use, Codex may require one `/hooks` review of
the user-level hook. Roundtable does not bypass that trust decision.

After trust is granted, a new Codex thread normally binds automatically: the
SessionStart hook atomically queues the native session identity, and the wake
bridge validates its exact project cwd and current fenced launcher lease before
recording the binding. The callback does not re-enter the app-server while the
thread is starting. Manual binding remains a troubleshooting fallback, not a
normal onboarding step:

```bash
rt-codex-wake bind /absolute/path/to/project
```

The Codex launcher also performs a targeted service preflight and claims the
project seat inside the same host lock as its final readiness check. A ready
pair is silent; an unambiguous cold daemon or stopped
wake bridge is repaired automatically. If the app-server definition or version
requires a coordinated reload, Roundtable offers it only when no active or
ambiguous Codex lease exists and the caller is not already inside Codex, then
asks before proceeding. Busy, unsupported, foreign, or unsafe states fail
closed with diagnostics. This replaces the old normal-user ritual of manually
running daemon and wake reload commands. The low-level commands remain expert
recovery tools.

The most common day-to-day commands are:

```text
roundtable                         project-first onboarding and launch
roundtable setup                  read-only harness integration preview
roundtable doctor                 diagnose setup, leases, and wake services
rt-say AGENT KIND "MESSAGE"       deliver durable mail
rt-inbox                          inspect waiting mail
rt-ack ID                         acknowledge a message
```

All participants in one Roundtable currently run on the same host. The durable
mailbox core does not require cmux and uses the same path in Terminal.app,
iTerm2, Ghostty, or another normal terminal; cmux supplies optional topology and
workspace affordances only. tmux lifecycle integration and cross-host SSH
transport are not P0 features.

The remaining Codex promotion gate is a real clean-account spike proving that
the trusted hook's `session_id` is the same native thread ID read through the
app-server and that the launcher's private runtime intent resolves to the same
current fenced lease, followed by credentialed send-to-wake-to-drain/ack E2E.
Until that passes, automatic binding is release-candidate behavior rather than
a public support claim.

## Development install

The current source tree can be installed into a versioned private environment.
This is for development and verification; the public judge path above uses the
artifact:

```bash
mamba run -n general ./scripts/install.sh
roundtable
```

`roundtable setup` remains a read-only preview. `roundtable setup apply`
configures only detected harnesses, or an explicit selection such as
`--harness claude --harness hermes --harness codex`. It merges owned hook or
plugin fragments, records backups and ownership, and links the installed
Roundtable skill into each selected harness's global skill directory. A normal
user does not copy or pull the skill into every project.

The menu can adopt the current non-Git directory without replacing user files,
select a registered project, choose another existing folder, or create a new
one. Git is always opt-in. Scriptable users can use `roundtable init`,
`roundtable claude`, `roundtable hermes`, or `roundtable codex`; the underlying
`roundtable-init` and `rt-*` commands remain available.

Stable commands are linked under `~/.local/bin`. Installation fails closed
when an existing path is not owned by its managed manifest. Remove harness
configuration before removing the package. If Codex was configured, run the
teardown from a normal terminal outside Codex so Roundtable can inspect and
unload only its two owned jobs:

```bash
roundtable-smoke
roundtable-setup status
roundtable-setup remove --unload-codex
roundtable-uninstall
```

Claude/Hermes-only onboarding uses plain `roundtable-setup remove`.
Uninstallation preserves the project registry, host runtime state, and every
project-local mailbox unless an explicit runtime purge is requested.

See [Installation and ownership](docs/install.md) for isolated preview paths,
offline release mode, upgrade gates, and precise removal behavior.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). Applicable material retained from the MIT-licensed h2o
predecessor keeps its full MIT notice there.
