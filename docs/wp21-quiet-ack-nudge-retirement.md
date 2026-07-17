# Quiet acknowledgements and per-agent delivery (WP2.1)

Roundtable mail remains the fact source. WP2.1 removes unnecessary wakeups and
keyboard nudges without changing message IDs or headers.

## Quiet sync acknowledgements

`rt-ack` still sends a `sync-ack` message whose header contains the normal
message ID, but its maildir filename is `ack-<msgid>.md`. Inbox readers accept
both that filename and the legacy `<msgid>.md` spelling for sync acknowledgements.
Wake and stop-gate consumers ignore `ack-*`; an already-awake agent can drain
them normally.

## Per-agent delivery

An agent entry in `.roundtable/agents.yaml` may set:

```yaml
agents:
  claude:
    delivery: maildir
```

`delivery: dual` is the compatibility default. `maildir` writes the message and
ledger record but never calls the cmux nudge path; normal stdout is
`sent maildir-only <msgid>`. Configured instances inherit their base agent's
delivery mode. `--no-nudge` remains an explicit one-shot mail-only override,
and `--legacy-nudge-only` remains the explicit keyboard-only emergency path.

The repository template and the active roundtable project configurations set
Claude (including configured Claude instances) to `maildir`; Hermes and Codex
remain `dual` until their separate retirement decisions.

App-server turns can lack a cmux caller and `RT_FROM`. For a maildir-only send,
`rt-say` may infer Codex from `CODEX_THREAD_ID` only when exactly one Codex
instance is configured, or when one instance has that exact `session_id`.
Ambiguous multi-instance configurations fail closed.

## Startup advisory

When `rt-watch-ensure` runs outside a roundtable project, it calls
`rt-startup-advisory`. If `CMUX_SURFACE_ID` identifies a caller whose workspace
has exactly one peer roundtable project, the helper prints one `cd` or
`ROUNDTABLE_PROJECT_DIR` suggestion. It never binds, routes, or writes state;
missing/ambiguous cmux identity and all operational failures are silent success.
