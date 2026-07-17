---
name: roundtable
description: >-
  Use when active roundtable coordination is required: an inbound [FROM→TO kind
  id=...] message arrives, the user mentions Hermes/Claude/Codex as peer agents,
  rt-say, rt-ack, rt-refresh, rt-resolve, handoff delivery, multi-instance agent
  routing, or cmux surface-routing bugs. Do not use merely because a repo
  contains .roundtable/agents.yaml.
version: 5.0.0
author: Roundtable contributors
license: MIT
platforms: [linux, macos]
---

# Roundtable

Collocated agents (Hermes, Claude, Codex) share one cmux workspace and talk through
the `rt-*` CLI tools. Address agents by **name**, never by surface id — the tools
resolve which surface an agent occupies and how to submit to it, and both drift as
agents restart and move.

## Tools (on PATH via ~/.roundtable/bin/)

| Tool | Purpose |
|------|---------|
| `rt-say <agent> <kind> "body"` | Send — resolves the surface, picks the submit key, tags it, blocks self-echo. |
| `rt-ack <id>[,<id>...] ["note"]` | Acknowledge received message(s). Comma-batches. |
| `rt-inbox` | List un-ack'd inbound messages. |
| `rt-resolve <agent>` | Print an agent's status + current surface ref. |
| `rt-refresh` | Rebuild the topology map (runtime.json) from the live cmux tree. |

Run them from a project root (a dir with `.roundtable/agents.yaml`). Outside one,
set `ROUNDTABLE_PROJECT_DIR` or `RT_FALLBACK_PROJECT` to point at a fallback project.

## Delivery v2: maildir + tripwire (dual-write period active since 2026-07-15)

`rt-say` now writes every message to
`<project>/.roundtable/inbox/<to>/new/<msgid>.md` **and** sends the legacy
keyboard nudge. The same message arriving twice (nudge text + maildir file) is
expected — dedupe by msgid. `--no-nudge` = maildir only; `--legacy-nudge-only`
= old behavior.

**Receiving (drain protocol)** — when woken by a tripwire or told the inbox has
mail: read every file in `inbox/<you>/new/`, act on each, `rt-ack` the ids
(comma-batch), `mv` the files to `inbox/<you>/cur/`, then **re-arm** the
tripwire before going idle. Files named `ack-*` are quiet delivery
confirmations: they never wake you and never block a stop — just read and `mv`
them to `cur/` whenever you are awake for another reason.

**Arming (Claude)** — run as a harness-tracked background process at the end of
any turn in a roundtable project:

```bash
rt-wait-inbox claude    # via run_in_background; exits when mail lands (or heartbeat)
```

No interval argument = adaptive heartbeat: 45m countdown that resets while the
session is active (rt-stop-gate stamps `.last-active` at every turn end), and
backs off to 240m after 6 consecutive empty beats (~4.5h true idle). Sub-hour
beats keep the prompt cache warm, so idle wakes and the eventual return both
stay cheap; pass an explicit interval only to pin special cases.

Its exit re-invokes you automatically — no keyboard, no human input. A Stop
hook (`rt-stop-gate`) blocks going idle with undrained mail or no live
tripwire; follow its stderr instruction, it is self-explanatory. Hermes arms
the same script via `terminal(background=true, notify_on_complete=true)`.
Codex delivery currently arrives via the nudge path (its wake bridge lands in
Phase 3).

**Replacing a tripwire: kill by marker, NEVER by name.** Other projects run
tripwires under the same process name; `pkill -f rt-wait-inbox` kills a
sibling project's tripwire and silently deafens that agent (real incident,
2026-07-17). To replace your own: read the pid from YOUR inbox's
`.armed-<pid>` marker and `kill` exactly that pid — or just arm a new one;
duplicates are harmless (first mail wakes both, both exit).

## Sending

Standard send sequence: **refresh → resolve → send**.

```bash
rt-refresh                    # 1. rebuild topology from live cmux
rt-resolve codex              # 2. verify target is mapped and where
rt-say codex question "..."   # 3. send
```

`rt-say` reads the existing topology map — it does NOT refresh internally
(refreshing inside rt-say can shuffle the map between your resolve and the
send, causing the message to go to the wrong surface). If you haven't
refreshed recently or agents restarted, refresh first.

`kind` is a free triage label (fyi, question, answer, proposal, review,
correction, directive, urgent) with no effect on delivery — pick the closest
and move on. For anything long, write `handoff/<topic>.md`, commit, and
rt-say a one-line pointer instead of pasting walls of text.

## Receiving

1. Inbound arrives as `[FROM→YOU kind id=<msg_id>] body`.
2. Do what it asks.
3. `rt-ack <msg_id> ["note"]` — batch with commas: `rt-ack id1,id2,id3`.

Ack because it's the sender's only delivery confirmation; un-ack'd, they can't tell
whether you saw it.

## Multi-instance

A harness can run more than one instance, each in its own **cmux-launched** surface.
Define them under `instances:` in `agents.yaml` and address each by its `id`
(verbatim, never auto-numbered); a single instance just reuses the base name
(`codex`). `rt-refresh` maps each instance to its surface from cmux's authoritative
`surface.list` binding, distinguishing same-harness instances by launch `cwd`
(primary anchor) then terminal `title`:

```yaml
instances:
  - { id: codex-build,  match: { cwd: /path/to/build } }
  - { id: codex-review, match: { title: review } }
```

Instances must be cmux-launched — that's how roundtable agents normally start. An
agent typed into a plain shell carries no cmux binding and won't be tracked.

## Raw cmux/tmux fallback

`rt-say` is a convenience wrapper, not a gate. If it's unavailable or misbehaving,
send directly with `cmux send` / `cmux send-key` (or `tmux send-keys`) — but do the
two things the wrapper normally handles for you:

1. **Resolve the surface first** (`rt-resolve <agent>`); cached ids go stale.
2. **Match the submit key to the target's state** — wrong key on a *busy* agent can
   submit into the wrong prompt:

   | Agent | Idle | Busy |
   |-------|------|------|
   | Claude | text + Enter | text only, no Enter (interrupt-safe) |
   | Codex | text + Enter | text + **Tab** (Enter submits the wrong prompt) |
   | Hermes | text + Enter | prepend `/steer`, then Enter (injects next turn) |

## When messages vanish

`rt-say` says `sent` but the target never reacts → the topology map was stale
when the send happened (a surface moved on restart, or rt-say used a cached
map from before the move). Recovery: `rt-refresh`, confirm with `rt-resolve`
or `cmux read-screen --surface <id>`, then resend. Most common failure — reach
for it first. Prevention: always `rt-refresh` before `rt-say` if you're unsure
the map is current.

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
