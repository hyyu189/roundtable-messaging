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
- Project identity does not depend on Git. Any directory can be a Roundtable
  project, including a non-code workspace.
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

## P0 state placement and session ownership

Roundtable separates project facts from facts that are meaningful only on one
host:

| State | Location | Lifetime |
| --- | --- | --- |
| Agent identities and project configuration | `<project>/.roundtable/` | Durable project state |
| Inbox `new/`, `cur/`, and `tmp/`; message ledger and acknowledgements | `<project>/.roundtable/` | Durable delivery state |
| Current session lease, owner PID and process fingerprint, wake-adapter PID, activity and heartbeat | `~/.roundtable/.runtime/` | Host-local ephemeral state |
| Optional terminal topology, navigation handles, and adapter diagnostics | `~/.roundtable/.runtime/adapters/` | Host-local ephemeral state |

Maildir `tmp/` is the deliberate exception to the simple durable/ephemeral
split: it is staging state, but it must remain on the same filesystem as
`new/` so publication can use an atomic rename.

P0 uses a deterministic key derived from the canonical project path, while
retaining that readable path in metadata:

```text
~/.roundtable/.runtime/
  projects/<canonical-path-hash>/
    project.json
    claim.lock
    agents/<agent-key>/
      state.lock
      lease.json
```

`agent-key` is the SHA-256 digest of the configured `agent_id`; the readable
identity remains inside `lease.json`. This keeps arbitrary configured IDs from
becoming paths. `RT_RUNTIME_DIR` may select another host-local root for tests
or managed installs; its legacy Codex alias must resolve to the same absolute
directory.

Runtime directories and files are private to the local user and updates use a
short host-local lock plus atomic replacement. Project-local Claude and Hermes
markers such as `.armed-<pid>`, `.last-active`, and `.empty-beats` are migrated
to the fenced lease record; old project-local markers are diagnostic-only.
Codex binding, bridge PID, heartbeat, locks, and logs are also host-local. The
optional cmux `runtime.json` and legacy operation locks follow the same
placement principle, but migrate in separate changes so they do not complicate
the session-ownership change.

### Logical seat, Roundtable session, and native session

These identities are intentionally different:

| Identity | Meaning | Reused |
| --- | --- | --- |
| `agent_id` | Stable project address and mailbox seat, such as `codex` | Yes |
| `session_id` | One Roundtable launch and ownership term | No |
| `native_session_id` | Harness-native Codex thread or equivalent, when available | Only for an explicit resume |
| `lease_revision` | Fencing token for the current owner of the seat | No |

The collision key is the logical seat `(project, agent_id)`, not the mere
existence of an old harness thread. P0 configures at most one logical seat per
harness in a project, so a second Codex launch currently resolves to the same
seat and is rejected while the first owner is active. Keying ownership by
`agent_id` leaves a compatible path for later projects with several named
instances of the same harness.

The launcher claims the seat before starting the harness and exports
`RT_PROJECT_ROOT`, `RT_FROM`, `RT_SESSION_ID`, and `RT_LEASE_REVISION`. The
anchored process always starts at the canonical project root, even when the
user invoked the launcher from a nested directory; this gives harness-native
thread binding one unambiguous project identity. The
lease names the harness owner process separately from its wake adapter or
tripwire. A live tripwire is not proof that the chat owns the seat, and a dead
tripwire does not make a still-running chat safe to replace. Hooks, watchers,
and bind operations may update or release a lease only when their session ID
and revision still match, preventing an old process from clearing a newer
owner. A stored Codex thread binding is routing metadata, not liveness proof,
and is valid only while it matches the current lease. Claim/reclaim takes the
seat's exclusive lock; operations that externally bind or wake a Codex thread
hold a shared fenced guard for their whole critical section so a new owner
cannot race an already-authorized old operation.
Pre-lease Codex bindings use the same project claim lock and are accepted only
when no Codex harness lease record exists anywhere in that project; the guard
remains held through binding or `turn/start`, so a legacy action and the first
unified claim also have a deterministic order.

Heartbeat reports adapter health; it is not by itself permission to steal a
seat. On the same host, owner PID plus a process-start fingerprint protects
against PID reuse and is the primary liveness proof. An unexpired-looking
heartbeat cannot keep a dead owner active, and an idle but live harness is not
declared dead merely because it has not emitted a recent heartbeat.

### Selector state machine

| Observed state | Selector behavior |
| --- | --- |
| No lease | Atomically claim the seat and start a fresh session |
| Owner live, adapter healthy | Report that the harness is already active and do not start a second session |
| Owner live, adapter unhealthy | Keep the seat occupied, report a wake-health problem, and direct the user to diagnostics |
| Owner dead or process fingerprint mismatched | Treat the lease as stale, atomically replace it, and start with a new session ID and revision |
| Liveness cannot be established safely | Fail closed and require an explicit repair or release action |

An inactive historical session is not a conflict. The default launch is fresh:
it gets a new Roundtable session ID and a new native chat/thread rather than
silently reconnecting to history. If a future selector offers an explicit
native-session resume, the new process still receives a new Roundtable session
ID and lease revision; only `native_session_id` is reused. P0 exposes only the
fresh path until each harness's native resume flow and project-root validation
have passed real end-to-end tests.

The mailbox remains addressed by stable `agent_id`, so queued mail survives a
session replacement and is drained by the new owner. Historical native IDs
may be retained as bounded local diagnostics, but routing and collision checks
consult only the current fenced lease.

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

1. add the host-local runtime helper and fenced seat lease, migrate
   Claude/Hermes liveness markers, and make the unified launcher selector
   enforce the state machine above;
2. add a dry-run-first, ownership-marked harness setup command with backups,
   idempotent merges, diagnostics, and a symmetric uninstall path;
3. install, validate, and safely reload the Codex app-server/wake bridge, then
   bind and exercise real npm and standalone threads;
4. wire and test Claude's stop gate, skill discovery, and tracked inbox
   tripwire;
5. wire and test the equivalent Hermes lifecycle;
6. run the same send-to-wake-to-ack acceptance in Terminal.app, iTerm2,
   Ghostty, and cmux;
7. test cmux topology, navigation, and notifications separately as optional
   adapter behavior.

Until steps 1–6 pass, the core is portable but the mainstream-terminal
experience is not yet claimed as complete.
