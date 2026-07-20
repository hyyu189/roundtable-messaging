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
| Codex | Shared executable resolver; global skill link; owned app-server and wake plist generation | Safe service load/reload, cwd-verified thread bind, and real send-to-wake-to-drain/ack |

The Codex plist files are written but not loaded by setup. This is an
intentional safety boundary, not evidence that the daemon is running.
Conversely, removing Codex onboarding requires
`roundtable-setup remove --unload-codex` from outside a Codex session, so a
loaded job cannot be orphaned after its plist and executable are removed.

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
- the daemon's CLI and app-server versions both equal the selected CLI version;
- the WebSocket-over-Unix-socket `initialize` / `initialized` handshake works.

Handshake liveness alone is not readiness. A daemon left running after a CLI
upgrade fails closed until it is reloaded and revalidated.

Future Codex releases are not accepted through an open-ended minimum version.
The app-server is an experimental integration surface, so each release is added
only after its protocol and end-to-end wake path pass.

## Validation matrix

| Codex distribution | CLI | App-server | Result |
| --- | ---: | ---: | --- |
| npm | `0.144.6` | isolated `0.144.6` | `initialize`, thread read/list, hooks list, and turn-history protocol smoke passed |
| npm | `0.144.6` | existing default daemon `0.144.5` | rejected as stale; reload and full wake E2E pending |
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

## Legacy and migration boundary

The current source tree replaces the earlier cmux keyboard-nudge delivery path.
cmux surface IDs and project-local `.armed-*` markers are not delivery or
liveness facts in Messaging v2. Existing maildir state remains durable, while
session leases and heartbeats live under the host-local
`~/.roundtable/.runtime/` tree.

An active installation created before the managed package and harness manifests
needs an explicit migration plan. The installer will not treat an unexplained
legacy path as owned, and setup will not overwrite a foreign config fragment,
plugin path, skill path, or plist.

## Official surface notes

OpenAI documents the standalone installer cache under
`$CODEX_HOME/packages/standalone`, with the visible command normally installed
under `~/.local/bin` on macOS and Linux. The public CLI supports `--remote` with
Unix-socket endpoints. OpenAI currently labels `codex app-server` experimental,
so Roundtable intentionally keeps an exact compatibility matrix instead of
assuming semver compatibility.
