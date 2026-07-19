# Roundtable Messaging v2

Roundtable is a local coordination layer for coding agents. Messaging v2 uses
durable per-project mailboxes as the delivery fact source and wakes supported
harnesses through native mechanisms instead of injecting keystrokes.

> Build status: the source installer has passed isolated clean-home tests. The
> checksummed release archive and offline dependency wheelhouse are still
> release gates; do not replace an active legacy installation without a planned
> migration.

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
| Terminal.app, iTerm2, and Ghostty | One first-class terminal baseline; automated core smoke passes, full harness wake UX matrix remains a release gate |
| npm Codex CLI `0.144.6` | Exact-release protocol smoke passed; clean daemon reload and full wake E2E remain a release gate |
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

## Development install

The current source tree can be installed into a versioned private environment:

```bash
mamba run -n general ./scripts/install.sh
```

Stable commands are linked under `~/.local/bin`. Installation is fail-closed
when an existing path is not owned by its managed manifest. Uninstallation
preserves the project registry, runtime state, and every project-local mailbox:

```bash
roundtable-smoke
roundtable-uninstall
```

See [Installation and ownership](docs/install.md) for isolated preview paths,
offline release mode, upgrade gates, and precise removal behavior.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
