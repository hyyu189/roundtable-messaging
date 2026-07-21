---
name: roundtable
description: >-
  Use when active roundtable coordination is required: an inbound [FROM→TO kind
  id=...] message arrives, the user mentions Hermes/Claude/Codex as peer agents,
  rt-say, rt-ack, rt-refresh, rt-resolve, handoff delivery, multi-instance agent
  routing, or cmux surface-routing bugs. Do not use merely because a repo
  contains .roundtable/agents.yaml.
version: 7.1.0
author: Roundtable contributors
license: MIT
platforms: [macos]
---

# Roundtable

Collocated agents (Hermes, Claude, Codex) collaborate per project and talk
through the `rt-*` CLI tools. Messages are **files in the project's mailbox**;
wakes are harness-native. Nothing touches a keyboard.

**Rule #0 — every collaborating session needs one project anchor.** One project
× one logical harness seat = one dedicated session = one mailbox. Every
identity mechanism keys off that canonical project path. For a human, prefer
the unified `roundtable` entry: it chooses or safely creates the project first,
then selects a configured harness seat. The scriptable `rt-claude`,
`rt-hermes`, and `rt-codex` launchers remain available. A project with no open
session for an agent means that agent is **offline** there; mail waits durably
in `new/`.

## One-time host setup

Package installation provides one canonical Roundtable skill. Onboarding links
that installed copy into each selected harness's global skill directory; do not
ask a vibe-coding user to clone, pull, or copy this skill per project.

For normal users, launch `roundtable`: it previews any missing integration for
the selected harness and asks once before applying owned changes. The
standalone setup commands are expert/scriptable controls:

```bash
roundtable setup          # read-only plan
roundtable setup apply    # owned hooks, plugin/skill links, Codex plists
roundtable setup status
```

Setup configures detected harnesses. Repeat `--harness` to make the selection
explicit. It never installs a harness or moves credentials. Codex plist files
are written but not loaded by setup. On the next project-anchored
`roundtable codex` launch, a service preflight starts a cold service or stopped
wake bridge automatically. It offers a coordinated app-server reload only from
outside Codex and only when no active or ambiguous Codex seat exists. Never
instruct an ordinary user to run the low-level daemon/wake reload commands.

For removal, never orphan a loaded Codex job. Ask the human to run this from a
normal terminal outside Codex:

```bash
roundtable-setup remove --unload-codex
roundtable-uninstall
```

The command refuses when called inside Codex and touches only Roundtable's two
owned labels. Claude/Hermes-only onboarding uses plain
`roundtable-setup remove`.

Any directory can become a project and Git is optional:

```bash
roundtable                         # recommended interactive entry
roundtable-init --here
roundtable-init new-project          # no Git by default
roundtable-init new-git-project --git
```

## Tools (normally linked on PATH via ~/.local/bin/)

| Tool | Purpose |
|------|---------|
| `roundtable` | Recommended project-first onboarding, harness selection, and launch. |
| `roundtable-setup [plan\|apply\|status\|remove]` | Own host-level harness onboarding; the default is a no-write plan. |
| `roundtable-init --here` / `roundtable-init NAME` | Adopt the current directory or create and register a project; add `--git` only when wanted. |
| `rt-claude` / `rt-hermes` / `rt-codex` | Claim a fenced project seat and launch the real harness executable. |
| `rt-say <agent> <kind> "body"` | Write the message into the target's project mailbox (atomic maildir). |
| `rt-ack <id>[,<id>...] ["note"]` | Acknowledge and archive received message(s). Comma-batches. The sender gets a quiet `ack-*` file. |
| `rt-inbox` | List un-ack'd inbound messages. |
| `rt-projects <list\|add\|rm>` | Maintain the validated project registry (single discovery source). |
| `rt-doctor` | Health checks: daemon, socket, RPC, version, bridge, registry, anchor audit. |
| `rt-resolve <agent>` / `rt-refresh` | Diagnostic only: where does cmux think an agent sits. Not part of sending. |

Run them from a project root (a dir with `.roundtable/agents.yaml`). Outside
one, set `ROUNDTABLE_PROJECT_DIR` or `RT_FALLBACK_PROJECT`.

Launch dedicated sessions with `rt-codex`, `rt-claude`, or `rt-hermes`. When
called outside a project on a TTY they offer registered projects, project
creation, or (for Claude/Hermes) an explicit unanchored launch. Roundtable
Codex requires a project anchor; native `codex` remains available for sessions
that do not need Roundtable messaging. Non-TTY unanchored calls exit 2. All
three launchers select a real harness executable instead of a generated cmux
PATH shim and export the unique configured `RT_FROM` identity. A
multi-instance project must set `RT_FROM` explicitly. `rt-codex` additionally
injects the `--remote` flag and fenced session environment that its native wake
bridge requires. Direct vendor launch commands do not establish the complete
lease context required for automatic wake; use the `rt-*` launchers for the
supported path.

## Delivery: maildir + native wake (v2, sole path since 2026-07-17)

`rt-say` atomically writes each message to
`<project>/.roundtable/inbox/<to>/new/<msgid>.md`. That write IS the delivery
— it needs no topology map, no live target, no refresh. `sync-ack` files are
named `new/ack-<msgid>.md`: quiet confirmations that never wake anyone and
never block a stop; drain them whenever you are awake for another reason.

**Receiving (drain protocol)** — when woken by a tripwire/bridge or told the
inbox has mail: run `rt-inbox -f json`, act on every non-ack message, then
`rt-ack` the ids (comma-batch). A successful `rt-ack` sends the quiet
confirmation and atomically archives those exact inbound files from `new/` to
`cur/`; a failed acknowledgement leaves them in `new/`. Move any quiet
`ack-*` files to `cur/` without acknowledging them. Hermes and Codex re-arm
automatically after the triggered non-ack generation is archived; Claude must
follow any re-arm instruction from its Stop hook.

**Arming (Claude)** — the setup-owned SessionStart hook launches the fenced
inbox watcher for a Roundtable-launched session. Its Stop hook prevents Claude
from going idle with undrained mail or a missing watcher. Follow the hook's
diagnostic instruction; ordinary users should not start or kill watcher
processes themselves.

**Arming (Hermes)** — the setup-owned plugin starts the fenced watcher at
Hermes session start and injects a user-visible Roundtable notice when mail
lands. It is inert outside a complete Roundtable launcher lease and shuts down
its watcher with the Hermes session.

**Arming (Codex)** — launch through project-anchored `roundtable codex` (or
`rt-codex`). The trusted SessionStart hook atomically queues the native thread
identity; the wake bridge validates its exact project cwd and fenced launcher
lease before binding. On first use Codex may ask the human to review the hook
with `/hooks`; never bypass that trust decision. Manual
`rt-codex-wake bind <project-root>` is a diagnostic fallback only. An unbound
session has no waker, but its mail still waits durably like any offline agent's.

`rt-wait-inbox` remains an implementation and diagnostic tool. Never kill it
by process name: another project can have the same executable name. P0 watcher
ownership is fenced by the host-local session lease; old project-local
`.armed-*`, `.last-active`, and `.empty-beats` files are diagnostic-only legacy
state and must not be used as routing or liveness truth.

## Sending

`rt-say <agent> <kind> "body"` from the project root. That's the whole ritual
— no refresh, no resolve, no liveness check. In a remote Codex app-server
turn, sender inference uses `CODEX_THREAD_ID`; outside a harness set
`RT_FROM`.

`kind` is a free triage label (fyi, question, answer, proposal, review,
correction, directive, urgent) with no effect on delivery. For anything long,
write `handoff/<topic>.md`, commit, and rt-say a one-line pointer.

Emergency keyboard path (`--legacy-nudge-only` + submit-key lore) is archived
in `~/.roundtable/docs/legacy-v1-keyboard.md`; human-coordinated use only.

## Receiving

1. Inbound arrives as a mail file `[FROM→YOU kind id=<msg_id>] body`.
2. Do what it asks.
3. `rt-ack <msg_id> ["note"]` — batch with commas. This both sends the
   sender's delivery confirmation and archives the processed inbound message.

## When mail sits unanswered

Mail waiting in `new/` means the receiver is offline or unarmed — not lost.
Diagnose in order: ① is a Roundtable-launched session open in that project
(Rule #0)? ② does `roundtable-setup status` report the harness configured?
③ does `rt-doctor` report a current fenced lease and healthy adapter?
④ for Codex, is the thread bound and are both services healthy? Fix the native
waker; never re-send by keyboard reflex.

## Multi-instance

A project can define more than one addressable instance ID under `instances:`
in `agents.yaml`; a single instance normally reuses the base name (`codex`).
Build Week P0 permits only one active seat per harness in a project, so a
second simultaneous Claude, Codex, or Hermes launch is rejected instead of
guessing. An inactive prior seat does not conflict: a fresh launch gets a new
fenced session lease. Mail addressing needs only the instance ID; cmux launch
metadata (cwd anchor, title) matters for the diagnostic `rt-resolve` view and
legacy tooling, not for delivery:

```yaml
instances:
  - { id: codex-build,  match: { cwd: /path/to/build } }
  - { id: codex-review, match: { title: review } }
```

## Collaboration discipline

- **The human lead arbitrates.** Agents propose; the human decides. Surface
  decisions; don't unilaterally enact irreversible ones.
- **No unauthorized intrusion.** Don't modify another harness's config, plugins,
  hooks, or orientation files without the human lead's authorization.
- **No ack-of-ack.** Once you receive a `sync-ack`, stop — don't acknowledge an
  acknowledgement.

## More

Optional multi-agent playbooks (cross-agent freeze/merge signoff, `/goal` build
dispatch, git-based doc collaboration) live in `~/.roundtable/docs/workflows/` —
not needed for ordinary messaging.
