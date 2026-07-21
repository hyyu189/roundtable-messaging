# Runtime compatibility

Roundtable's durable maildir core and its harness wake adapters have different
compatibility boundaries. Delivery can succeed while an offline or unsupported
harness remains unwoken.

## Harness onboarding matrix

`roundtable-setup` configures harnesses already installed by the user. It does
not install a harness, create an account, copy credentials, or certify that a
real vendor session can wake.

| Harness | Packaged and automated | Still required before support promotion |
| --- | --- | --- |
| Claude Code | Global skill link; owned SessionStart inbox hook; owned Stop drain gate; plan/apply/status/remove tests | Clean-account skill discovery and real send-to-wake-to-drain/ack |
| Hermes | Global skill link; packaged lifecycle plugin; marked plugin enablement; plan/apply/status/remove tests | Clean-account plugin load/injection and real send-to-wake-to-drain/ack |
| Codex | Shared executable resolver; global skill link; owned SessionStart auto-bind hook; owned app-server and wake plist generation; fail-closed service preflight tests | Live hook identity spike, coordinated host cutover, and real send-to-wake-to-drain/ack |

The Codex plist files are written but not loaded by setup. This is an
intentional safety boundary, not evidence that the daemon is running.
Conversely, removing Codex onboarding requires
`roundtable-setup remove --unload-codex` from outside a Codex session, so a
loaded job cannot be orphaned after its plist and executable are removed.

The normal `roundtable` launcher owns the next step: it performs a targeted
Codex service preflight and starts or repairs only states proven safe. Users do
not normally run the two low-level service reload commands themselves.

## Codex executable selection

Every Codex-facing component uses the same resolver:

1. explicit `RT_CODEX_BIN`;
2. the official standalone cache at
   `$CODEX_HOME/packages/standalone/current/codex`;
3. the common npm installation at `~/.npm-global/bin/codex`;
4. `~/.local/bin/codex`;
5. a `codex` executable on the controlled fallback PATH.

The selected path is preserved rather than dereferenced. This matters for both
the standalone `current` link and npm's visible CLI shim. The launcher,
app-server LaunchAgent, wake LaunchAgent, daemon checks, and doctor all use that
same path.

One static custom `CODEX_HOME` is supported when it is an absolute owned path
under the user's home and is present consistently during setup and launch. Its
hook, skill link, standalone resolver, socket, and LaunchAgent environment all
use that same root. Switching `CODEX_HOME` between identities on individual
launches is multi-auth lifecycle management and remains outside P0; setup and
preflight fail closed on the resulting ownership drift.

## Terminal launcher portability

`rt-claude`, `rt-hermes`, and `rt-codex` execute absolute harness paths. The
Claude and Hermes resolvers prefer their normal user-level installations, then
search PATH while rejecting cmux's generated `cmux-cli-shims` and wrapper
targets. `RT_CLAUDE_BIN`, `RT_HERMES_BIN`, and `RT_CODEX_BIN` provide explicit
selection; an explicit Claude or Hermes path is still rejected if it resolves
to a cmux wrapper.

Inside a Roundtable project, each launcher exports `RT_FROM` automatically when
exactly one configured instance uses that harness. A multi-instance
configuration must select its identity explicitly, for example
`RT_FROM=claude-review rt-claude`. This identity path is configuration-based
and does not require a cmux surface, so it works in ordinary terminal apps.
The cmux topology commands remain optional integration tools; full tmux support
is not claimed until its end-to-end gate passes.

## Readiness contract

Codex wake is ready only when all of the following are true:

- the selected CLI is an explicitly validated release;
- the daemon reports `running`;
- the requested and reported Unix sockets match;
- the daemon's `managedCodexPath` exactly identifies the selected executable;
- the daemon's CLI and app-server versions both equal the selected CLI version;
- no authenticated, digest-bound setup marker says the current app-server
  plist is still awaiting activation or reload;
- the wake heartbeat reports the fingerprint of the currently installed bridge
  and its local dependencies;
- the WebSocket-over-Unix-socket `initialize` / `initialized` handshake works.

Handshake liveness alone is not readiness. A daemon left running after a CLI
upgrade fails closed until it is reloaded and revalidated.

Future Codex releases are not accepted through an open-ended minimum version.
The app-server is an experimental integration surface, so each release is added
only after its protocol and end-to-end wake path pass.

## Codex service preflight

The Codex launcher checks the service pair before publishing a project-seat
lease. Its state machine is deliberately narrower than a generic repair tool:

| State | Launcher behavior |
| --- | --- |
| `ready` | Continue silently |
| `cold` | Under a host repair lock, re-check and start only a clear liveness failure |
| `bridge_down` | Revalidate the app-server, then restart only the wake bridge |
| `reload_required_idle` | Explain possible disconnection and ask before coordinated reload |
| `reload_deferred_busy` | Refuse because the caller or another active, unhealthy-live, or ambiguous Codex lease may be disrupted |
| `setup_required` | Stop and direct the user to managed setup |
| `unsupported` | Stop because the selected Codex release has not passed this protocol matrix |
| `unsafe` | Stop on foreign plist/socket ownership, permissions, malformed runtime state, or non-liveness protocol failure |

Every launch takes one host-wide repair lock plus the install setup-state lock
and re-runs the inspection inside them. The final `ready` observation and
project-seat claim happen before those locks are released, so a concurrent
setup or reload cannot slip between them. A version or owned-plist mismatch is
never interpreted as permission to silently restart the shared app-server.
Roundtable Codex therefore requires a project anchor in P0;
unanchored users can run native `codex` without Roundtable messaging. True
zero-downtime upgrades would require versioned blue/green sockets and are P1
rather than a P0 claim.

## Codex automatic binding

The owned Codex SessionStart hook matches `startup`, `resume`, and `clear`, but
not `compact`. It only writes a private atomic request and exits; it never calls
the app-server recursively during thread startup. The wake bridge consumes that
request later and accepts it only when all of these identities agree:

- the hook's native session/thread ID exists in the app-server;
- the app-server reports the exact canonical project cwd;
- the thread is an interactive root thread, not a child or ephemeral thread;
- the request's project, agent ID, Roundtable session ID, and lease revision
  match the current fenced host lease;
- the project has not acquired a conflicting current binding.

Exact replays are idempotent. A trusted `clear` event may move the same current
lease to its replacement native thread; a request from an older lease cannot
replace a newer claim. If `clear` replaces a request while the bridge is
draining it, the bridge quarantines the superseded binding and processes the
replacement request before it can wake pending mail. User-level Codex hooks may
require a one-time `/hooks` trust review, and Roundtable does not bypass that
decision. The manual
`rt-codex-wake bind /absolute/project/path` command remains a diagnostic
fallback.

The launcher thread's first `startup` or `resume` request wins for its lease;
an interactive Codex started later from one of that thread's tool shells cannot
replace it merely by sharing the project cwd. A `clear` event is allowed to
replace the current native thread for the same lease. P0 treats an
operator deliberately running `/clear` inside a nested Codex that inherited
that lease as a same-user cooperative boundary, not as a supported nested-Codex
routing topology; stronger per-client lifecycle identity is deferred to P1.

This path has focused automated coverage, but it is not yet a public support
claim. After the live machine cutover, a real spike must prove that Codex's
SessionStart `session_id` is the same ID returned by `thread/read` and that the
private runtime launch intent resolves to the same current fenced lease. That
is followed by the complete credentialed send-to-wake-to-drain/ack gate.

## Validation matrix

| Codex distribution | CLI | App-server | Result |
| --- | ---: | ---: | --- |
| npm | `0.144.6` | isolated `0.144.6` | `initialize`, thread read/list, hooks list, and turn-history protocol smoke passed |
| npm | `0.144.6` | existing default daemon `0.144.5` | rejected as stale; the development machine has not yet performed the coordinated cutover or live hook/E2E gate |
| standalone | not installed | not installed | resolver and fixtures only; support is not yet claimed |
| any future or unlisted release | any | any | rejected until explicitly validated |

Before the Build Week release, npm `0.144.6` still needs the clean default
daemon and real send-to-wake-to-drain/ack gate. Standalone support requires an
official standalone installation followed by the same gate; an app-bundled
internal Codex binary does not qualify as the standalone distribution.

## Terminal acceptance matrix

The core smoke runs without a terminal adapter and proves durable send, inbox,
acknowledgement, and drain. It does not prove interactive wake UX.

| Host | Core transport | Real Claude/Hermes/Codex wake |
| --- | --- | --- |
| Terminal.app | Same maildir core | Pending promotion gate |
| iTerm2 | Same maildir core | Pending promotion gate |
| Ghostty | Same maildir core | Pending promotion gate |
| cmux | Same maildir core; optional topology features | Pending baseline and separate optional-adapter gate |
| tmux | Core design is reusable | Unsupported until lifecycle and wake E2E pass |
| Cross-host SSH | No P0 transport | Unsupported |

Every supported P0 participant currently shares one host filesystem and one
host-local runtime root. This is independent of terminal emulator: Terminal,
iTerm2, Ghostty, and cmux can all reach the same maildir and harness adapters.
It is not cross-host transport. Two coding sessions on different Macs do not
share a Roundtable merely because both were launched over SSH; a future
cross-host transport must preserve durable delivery and identity fencing across
machines.

## Legacy delivery boundary

The current source tree replaces the earlier cmux keyboard-nudge delivery path.
cmux surface IDs and project-local `.armed-*` markers are not delivery or
liveness facts in Messaging v2. Existing maildir state remains durable, while
session leases and heartbeats live under the host-local
`~/.roundtable/.runtime/` tree.

## Official surface notes

OpenAI documents the standalone installer cache under
`$CODEX_HOME/packages/standalone`, with the visible command normally installed
under `~/.local/bin` on macOS and Linux. The public CLI supports `--remote` with
Unix-socket endpoints. OpenAI currently labels `codex app-server` experimental,
so Roundtable intentionally keeps an exact compatibility matrix instead of
assuming semver compatibility.
