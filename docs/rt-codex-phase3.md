# Codex app-server wake bridge (Phase 3)

Implementation target: Codex CLI `0.144.3`. The wire schema and wake path are
version-gated to that exact release.

## Topology

The npm Codex installation cannot use `codex app-server daemon start`: that
manager requires the standalone installer at
`~/.codex/packages/standalone/current/codex`. Roundtable therefore owns a user
LaunchAgent (`com.roundtable.codex-app-server`) which runs the installed Codex
CLI directly as:

```text
codex app-server --listen unix://$HOME/.codex/app-server-control/app-server-control.sock
```

`rt-codex-daemon ensure` performs a real initialize/initialized handshake, uses
a single-flight startup lock, installs the LaunchAgent if absent, kickstarts it,
and waits for the handshake to recover. Socket existence alone is not treated
as health.

A bare scratch TUI with no config overrides auto-detected the default daemon.
The isolated proof correlated the TUI's Unix fd peer with the LaunchAgent's
named control socket via `lsof`. The production cmux wrapper is different: it
injects per-launch `-c` and hook-trust flags, which make the implicit TUI probe
ineligible. `rt-codex` therefore forces `--remote unix://` while preserving the
normal `codex`/cmux wrapper path.

## Bridge safety contract

`rt-codex-wake`:

- watches explicit roots, `RT_CODEX_PROJECTS`, or the default `~/RL/*` plus
  `RT_FALLBACK_PROJECT`;
- treats maildir `new/` as the fact source and never moves or deletes mail;
- validates every mail header/id/target and rejects symlinks or non-files;
- binds a project to one root, non-ephemeral TUI thread and persists the mapping
  (`cli`, or app-server 0.144.3's `vscode` + `threadSource=user` for `--remote`);
- prefers `CODEX_THREAD_ID` self-registration (`rt-codex-wake bind`); only an
  embedded local `cli` thread may be auto-discovered, because app-server's
  `vscode` source cannot distinguish a remote TUI from a real IDE session;
- resumes and revalidates the bound thread after every daemon reconnect;
- starts one short pointer turn for a non-empty generation only when status is
  idle; an active thread waits for its matching `turn/completed` notification;
- persists generation/turn state, uses bounded exponential retry for an
  interrupted, failed, ambiguous, or completed-but-undrained wake, and leaves
  every message in `new/` on failure;
- uses a kernel `flock` singleton (released even on SIGKILL) and locked atomic
  state writes so a live bridge cannot overwrite an external rebind;
- revalidates the CLI/app-server version on every connection and stops issuing
  wake RPCs outside the exact validated release;
- writes only metadata to its ignored runtime log, never mail bodies.

The app-server currently has no atomic “start only if idle” RPC. The immediate
status recheck minimizes, but cannot eliminate, the status-to-`turn/start`
TOCTOU window because `turn/start` can steer an active regular turn. Strict
elimination requires an upstream conditional wake/CAS method.

App-server human-decision requests are multicast to thread subscribers and the
first response wins. The bridge deliberately does not race the TUI with
synthetic approval or user-input responses. If no TUI is present, the wake can
remain active; the original mail remains pending and the event is logged.

## Operations

```bash
# daemon persistence and health
~/.roundtable/bin/rt-codex-daemon install
~/.roundtable/bin/rt-codex-daemon ensure

# required from a remote target Codex turn at the coordinated cutover
~/.roundtable/bin/rt-codex-wake bind /absolute/project/root

# install the bridge; repeat --project for roots outside the default scan
~/.roundtable/bin/rt-codex-wake install --project /absolute/project/root

# five operational checks, nonzero only for FAIL (WARN is legacy fallback)
~/.roundtable/bin/rt-doctor
```

Installing the bridge does not migrate an already-running embedded TUI. The
current cmux Codex session must only be restarted through `rt-codex` during the
separately coordinated production cutover.

## Verification

The regression suite covers WebSocket-over-UDS framing and the exact initialize
envelope, multi-mail single wake, busy completion gating, fail-closed identity
and mail validation, failed/undrained wake backoff, daemon-resume recovery,
crash-safe singleton locking, concurrent rebind preservation, exact version
gating, launchd root persistence, daemon self-heal orchestration, and doctor
failure/WARN/socket-mismatch states.

```bash
cd ~/.roundtable
mamba run -n general python -m pytest -p no:rerunfailures -q \
  tests/test_rt_codex.py tests/test_rt_tooling.py
```

The local `pytest-rerunfailures` plugin opens a TCP status socket during pytest
configuration, so it is disabled above; the app-server regression itself uses
only an isolated Unix socket.

### Isolated live acceptance (2026-07-17)

The production cmux Codex session was not restarted. All four acceptance paths
ran against `/private/tmp/rt-phase3-accept-20260717` and its scratch TUI:

1. A `--no-nudge` message woke an idle remote TUI, created
   `acceptance-1.txt`, emitted a quiet ack, and moved the mail from `new/` to
   `cur/` without keyboard input.
2. A message delivered during a controlled 12-second turn remained pending.
   The bridge started its one wake only after `turn/completed`, then created
   `acceptance-2.txt` and drained the mail.
3. The app-server LaunchAgent was booted out (old PID `91975`). The next
   pending delivery caused the bridge startup path to reinstall and start the
   daemon (new PID `92782`), resume the persisted thread, create
   `acceptance-3.txt`, acknowledge it, and return the project state to
   `EMPTY`.
4. Live `rt-doctor` reported daemon, socket ownership, JSON-RPC handshake,
   exact version, and bridge heartbeat as `OK`. Automated tests exercise the
   daemon/socket/RPC/bridge failure matrix, unsupported-version `WARN`, and
   socket/plist drift remediation.

Final verification: `100 passed` in the combined Phase 3 and tooling suite.
