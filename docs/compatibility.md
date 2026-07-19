# Runtime compatibility

Roundtable's durable maildir core and its harness wake adapters have different
compatibility boundaries. Delivery can succeed while an offline or unsupported
harness remains unwoken.

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

## Official surface notes

OpenAI documents the standalone installer cache under
`$CODEX_HOME/packages/standalone`, with the visible command normally installed
under `~/.local/bin` on macOS and Linux. The public CLI supports `--remote` with
Unix-socket endpoints. OpenAI currently labels `codex app-server` experimental,
so Roundtable intentionally keeps an exact compatibility matrix instead of
assuming semver compatibility.
