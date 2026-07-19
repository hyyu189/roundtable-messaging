# Quiet acknowledgements and per-agent delivery (WP2.1)

Roundtable mail remains the fact source. WP2.1 removes unnecessary wakeups and
keyboard nudges without changing message IDs or headers.

## Quiet sync acknowledgements

`rt-ack` still sends a `sync-ack` message whose header contains the normal
message ID, but its maildir filename is `ack-<msgid>.md`. Inbox readers accept
both that filename and the legacy `<msgid>.md` spelling for sync acknowledgements.
Wake and stop-gate consumers ignore `ack-*`; an already-awake agent can drain
them normally.

## Sole normal delivery path

This document originally introduced per-agent `maildir` versus `dual`
delivery. That transition is complete: normal `rt-say` is now always
maildir-only for every harness and configured instance. Old `delivery` fields
do not re-enable a nudge. Normal stdout is `sent maildir-only <msgid>`.

`--no-nudge` is retained as a compatibility alias with exactly the same
behavior as normal `rt-say`. `--legacy-nudge-only` is the separate,
human-coordinated keyboard emergency path and never writes maildir state.

App-server turns can lack `RT_FROM`. For a maildir-only send,
`rt-say` may infer Codex from `CODEX_THREAD_ID` only when exactly one Codex
instance is configured, or when one instance has that exact `session_id`.
Ambiguous multi-instance configurations fail closed.

## Startup advisory

`rt-startup-advisory` is an optional cmux integration. If
`CMUX_SURFACE_ID` identifies a caller whose workspace has exactly one peer
Roundtable project, the helper prints one `cd` or `ROUNDTABLE_PROJECT_DIR`
suggestion. It never binds, routes, or writes state; missing or ambiguous cmux
identity and all operational failures are silent success. The retired
`rt-watch-ensure` path is not part of current startup or delivery.
