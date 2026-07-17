---
name: roundtable
description: >-
  Use when active roundtable coordination is required: an inbound [FROM→TO kind
  id=...] message arrives, the user mentions Hermes/Claude/Codex as peer agents,
  rt-say, rt-ack, rt-refresh, rt-resolve, handoff delivery, multi-instance agent
  routing, or cmux surface-routing bugs. Do not use merely because a repo
  contains .roundtable/agents.yaml.
version: 6.0.0
author: Roundtable contributors
license: MIT
platforms: [linux, macos]
---

# Roundtable

Collocated agents (Hermes, Claude, Codex) collaborate per project and talk
through the `rt-*` CLI tools. Messages are **files in the project's mailbox**;
wakes are harness-native. Nothing touches a keyboard.

**Rule #0 — launch every agent in its project root.** One project × one
harness = one dedicated session = one mailbox. Every identity mechanism
(orientation autoload, rt-tool project discovery, tripwire/inbox anchoring,
codex thread binding) keys off the directory the agent was started in; an
agent launched elsewhere (e.g. `~`) is anchor-less — gates no-op, binds fail,
workarounds pile up. `rt-startup-advisory` warns about this at session start.
A project with no open session for an agent = that agent is **offline** there;
mail waits in `new/` (the mailbox is the fact source).

## Tools (on PATH via ~/.roundtable/bin/)

| Tool | Purpose |
|------|---------|
| `rt-say <agent> <kind> "body"` | Write the message into the target's project mailbox (atomic maildir). |
| `rt-ack <id>[,<id>...] ["note"]` | Acknowledge received message(s). Comma-batches. Lands as a quiet `ack-*` file. |
| `rt-inbox` | List un-ack'd inbound messages. |
| `rt-projects <list\|add\|rm>` | Maintain the validated project registry (single discovery source). |
| `rt-doctor` | Health checks: daemon, socket, RPC, version, bridge, registry, anchor audit. |
| `rt-resolve <agent>` / `rt-refresh` | Diagnostic only: where does cmux think an agent sits. Not part of sending. |

Run them from a project root (a dir with `.roundtable/agents.yaml`). Outside
one, set `ROUNDTABLE_PROJECT_DIR` or `RT_FALLBACK_PROJECT`.

Launch dedicated sessions with `rt-codex`, `rt-claude`, or `rt-hermes`. When
called outside a project on a TTY they offer registered projects, project
creation, or an explicit unanchored launch; non-TTY unanchored calls exit 2.

## Delivery: maildir + native wake (v2, sole path since 2026-07-17)

`rt-say` atomically writes each message to
`<project>/.roundtable/inbox/<to>/new/<msgid>.md`. That write IS the delivery
— it needs no topology map, no live target, no refresh. `sync-ack` files are
named `new/ack-<msgid>.md`: quiet confirmations that never wake anyone and
never block a stop; drain them whenever you are awake for another reason.

**Receiving (drain protocol)** — when woken by a tripwire/bridge or told the
inbox has mail: read every file in `inbox/<you>/new/`, act on each, `rt-ack`
the ids (comma-batch), `mv` the files to `inbox/<you>/cur/`, then **re-arm**
before going idle.

**Arming (Claude)** — run as a harness-tracked background process at the end
of any turn in a roundtable project:

```bash
rt-wait-inbox claude    # via run_in_background; exits when mail lands (or heartbeat)
```

No interval argument = adaptive heartbeat: 45m countdown that resets while the
session is active (rt-stop-gate stamps `.last-active` at every turn end), and
backs off to 240m after 6 consecutive empty beats (~4.5h true idle). Sub-hour
beats keep the prompt cache warm. Its exit re-invokes you automatically. A
Stop hook (`rt-stop-gate`) blocks going idle with undrained mail or no live
tripwire; follow its stderr instruction.

**Arming (Hermes)** — same script via
`terminal(background=true, notify_on_complete=true)`.

**Arming (Codex)** — launch from the project root with `rt-codex` (daemon
liveness and remote attach handled automatically), then self-register in the
first turn: `rt-codex-wake bind <project-root>`. The wake bridge then delivers
maildir wakes with zero keyboard. A codex session that never self-registered
has no waker — its mail waits like any offline agent's.

**Replacing a tripwire: kill by marker, NEVER by name.** Other projects run
tripwires under the same process name; `pkill -f rt-wait-inbox` deafens a
sibling project's agent (real incident, 2026-07-17). Read the pid from YOUR
inbox's `.armed-<pid>` marker and `kill` exactly that pid — or just arm a new
one; duplicates are harmless.

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
3. `rt-ack <msg_id> ["note"]` — batch with commas. Ack because it's the
   sender's only delivery confirmation.

## When mail sits unanswered

Mail waiting in `new/` means the receiver is offline or unarmed — not lost.
Diagnose in order: ① is a session open in that project (Rule #0)? ② Claude/
Hermes: is a tripwire armed (`.armed-*` marker with a live pid)? ③ Codex: is
the thread bound (`rt-doctor` anchor audit / bridge heartbeat)? ④ `rt-doctor`
for daemon/bridge health. Fix the waker; never re-send by keyboard reflex.

## Multi-instance

A harness can run more than one instance per project. Define them under
`instances:` in `agents.yaml` and address each by its `id` (verbatim); a
single instance reuses the base name (`codex`). Mail addressing needs only
the name; cmux launch metadata (cwd anchor, title) matters for the diagnostic
`rt-resolve` view and for legacy tooling, not for delivery:

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
