# Roundtable Messaging v2

Roundtable is a local coordination layer for coding agents. Messaging v2 uses
durable per-project mailboxes as the delivery fact source and wakes supported
harnesses through native mechanisms instead of injecting keystrokes.

> Build status: the public Build Week repository is being assembled from an
> audited implementation history. Installation instructions will replace this
> notice after the first clean-release smoke test.

## Why it exists

Multi-agent terminal workflows become fragile when delivery depends on pane
focus, keyboard timing, or a particular terminal multiplexer. Roundtable
separates durable delivery from wake-up:

1. a sender atomically writes a message into the recipient's project mailbox;
2. an optional harness adapter wakes an online recipient;
3. the recipient drains, acknowledges, and archives the message;
4. an offline recipient keeps the message until it returns.

The intended core path works in a normal macOS terminal without cmux. cmux
remains an optional integration rather than a transport dependency.

## Build Week scope

The submitted Messaging v2 architecture and deliverable were built during the
2026 OpenAI Build Week submission period, replacing an earlier keyboard-based
prototype. The repository keeps the earlier baseline only where it is required
to make the rewrite reviewable.

- [Development and attribution boundary](PROVENANCE.md)
- [Contributor roles](CREDITS.md)
- [Source commit ledger](docs/provenance/source-commits.tsv)

Ocean directed the product. GPT-5.6 through Codex was the primary implementation
environment. Fable 5 contributed specified early code, documentation,
configuration, design, and review; those contributions are recorded
commit-by-commit rather than described as GPT-5.6-only work.

All productization work begun in this public repository is GPT-5.6/Codex-led.

## Release target

The Build Week P0 release is complete only when it provides:

- an idempotent user-level installer and precise uninstaller;
- a five-minute judge path from a packaged release;
- verified support for the current npm Codex and an honestly tested standalone
  path;
- a no-cmux terminal end-to-end path;
- accurate diagnostics, recovery, tests, and public-safety checks.

Same-host tmux support is P1. Cross-host transport, Linux service management,
and multi-auth switching are roadmap items.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE).
