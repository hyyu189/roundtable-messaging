# Architecture and adapter boundaries

Roundtable is terminal-emulator independent by design. Terminal.app, iTerm2,
Ghostty, and other terminal hosts are not separate transports or reduced
compatibility modes. They host harness processes that use the same Roundtable
core.

## Layers

| Layer | Responsibility | Required for delivery |
| --- | --- | --- |
| Product core | Project config and identity, atomic maildir delivery, ledger, inbox, acknowledgement, and drain state | Yes |
| Harness adapters | Codex app-server wake; Claude and Hermes tripwire/hook integration | Only for automatic wake; offline delivery still succeeds |
| Terminal integrations | Optional workspace topology, surface diagnostics, project navigation, and notifications | No |

The data path is:

```text
sender harness
  -> rt-say
  -> project maildir (delivery fact)
  -> recipient inbox
  -> harness-native wake adapter, when the recipient is online

optional cmux adapter
  -> observes project/workspace topology
  -> never owns the delivery fact
```

## Invariants

- Delivery, inbox, acknowledgement, and drain do not call a terminal-emulator
  API.
- Harness launchers resolve real executables and reject generated cmux PATH
  shims.
- A missing or unhealthy optional terminal integration cannot invalidate a
  maildir delivery.
- cmux topology state may improve navigation and diagnostics, but it is never
  authoritative for whether a message exists.
- Human-attention alerts fall back to native macOS notifications; cmux may add
  workspace-aware notification context but is not the only alert provider.
- Terminal-emulator support and harness support are separate axes. A Codex
  app-server compatibility gate is not a Ghostty, iTerm2, or Terminal.app
  compatibility gate.

## First-class terminal baseline

A terminal host is first-class when a clean installation can:

1. launch each configured harness in its project with the correct identity;
2. send and inspect durable mail while the recipient is offline;
3. wake an online recipient through the harness adapter, not injected keys;
4. acknowledge and drain the message;
5. diagnose and recover the harness adapter without installing cmux.

`roundtable-smoke` automates the core portion in an isolated environment with
no optional terminal adapter loaded. The remaining release gate is the real
Claude, Hermes, and Codex wake/UX matrix in Terminal.app, iTerm2, and Ghostty.
cmux must pass the same baseline and may additionally expose its optional
workspace features.

tmux is a multiplexer rather than a terminal emulator. Same-host tmux and
cross-host SSH require their own lifecycle and wake acceptance before support
is claimed; neither should fork the core transport.

## Current implementation boundary

The release installer currently installs versioned commands, templates, and
the Roundtable skill. It does not yet merge Claude or Hermes hook
configuration, install Codex wake services, bind a Codex thread, or prove that
each harness can discover the installed skill from a clean account. Those are
harness-onboarding gaps, not terminal-emulator dependencies.

The P0 implementation order is therefore:

1. add a dry-run-first, ownership-marked harness setup command with backups,
   idempotent merges, diagnostics, and a symmetric uninstall path;
2. install, validate, and safely reload the Codex app-server/wake bridge, then
   bind and exercise real npm and standalone threads;
3. wire and test Claude's stop gate, skill discovery, and tracked inbox
   tripwire;
4. wire and test the equivalent Hermes lifecycle;
5. run the same send-to-wake-to-ack acceptance in Terminal.app, iTerm2,
   Ghostty, and cmux;
6. test cmux topology, navigation, and notifications separately as optional
   adapter behavior.

Until steps 1–5 pass, the core is portable but the mainstream-terminal
experience is not yet claimed as complete.
