"""Hermes user-plugin bridge for Roundtable's durable inbox.

The plugin is deliberately inert unless it was launched through a Roundtable
launcher that supplied a complete, fenced session environment.  It never reads
message bodies or credentials.  ``rt-wait-inbox`` remains responsible for
validating the lease and watching the durable maildir.
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any


_REQUIRED_ENV = (
    "RT_PROJECT_ROOT",
    "RT_FROM",
    "RT_SESSION_ID",
    "RT_LEASE_REVISION",
)
_MAIL_MARKER = "rt-wait-inbox: mail after "
_HEARTBEAT_MARKER = "rt-wait-inbox: heartbeat timeout after "
_SUPERSEDED_MARKER = "rt-wait-inbox: seat lease or watcher was superseded"
_MAIL_DRAIN_POLL_SECONDS = 0.25
_STOP_JOIN_SECONDS = 2.0

_MAIL_MESSAGE = (
    "[Roundtable] New durable mail is waiting. Run `rt-inbox`, process the "
    "non-ack messages, and acknowledge them with `rt-ack`."
)
_FENCE_MESSAGE = (
    "[Roundtable] This Hermes watcher stopped because its session lease was "
    "superseded. Continue from the newer Roundtable-launched session."
)
_CONFIG_MESSAGE = (
    "[Roundtable] Inbox watching stopped because the Roundtable session or "
    "installation is invalid. Run `rt-doctor` before restarting Hermes."
)


def _activation_environment() -> dict[str, str] | None:
    """Return a stable environment snapshot, or ``None`` when not opted in."""

    values = {name: os.environ.get(name, "").strip() for name in _REQUIRED_ENV}
    if not all(values.values()):
        return None
    environment = os.environ.copy()
    environment.update(values)
    return environment


def _resolve_waiter(environment: dict[str, str]) -> str | None:
    """Resolve the installed waiter once, preferring the managed prefix."""

    prefix = environment.get("ROUNDTABLE_INSTALL_PREFIX", "").strip()
    if prefix:
        candidate = (
            Path(prefix).expanduser() / "bin" / "rt-wait-inbox"
        )
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return shutil.which("rt-wait-inbox", path=environment.get("PATH"))


def _pending_non_ack_mail(project_root: Path, agent: str) -> bool:
    new_dir = project_root / ".roundtable" / "inbox" / agent / "new"
    try:
        return any(
            not entry.name.startswith(("ack-", "."))
            for entry in new_dir.iterdir()
        )
    except FileNotFoundError:
        return False
    except OSError:
        # Fail closed: do not start another watcher while the durable inbox
        # cannot be inspected.
        return True


class _RoundtableBridge:
    """Own one fenced watcher process and its daemon supervisor thread."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._closed = False
        self._diagnostic_sent = False

    def on_session_start(self, **_kwargs: Any) -> None:
        environment = _activation_environment()
        if environment is None:
            return

        with self._lock:
            if self._closed or (
                self._thread is not None and self._thread.is_alive()
            ):
                return
            waiter = _resolve_waiter(environment)
            if waiter is None:
                self._inject_diagnostic(_CONFIG_MESSAGE)
                self._closed = True
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._watch,
                args=(environment, waiter),
                name="roundtable-hermes-watcher",
                daemon=True,
            )
            self._thread.start()

    def on_session_finalize(self, **_kwargs: Any) -> None:
        # Hermes can finalize one native session and start another inside the
        # same CLI process (for example after /new). Stop the old fenced
        # watcher without permanently disabling this plugin instance.
        self._shutdown(permanent=False)

    def close(self) -> None:
        self._shutdown(permanent=True)

    def _shutdown(self, *, permanent: bool) -> None:
        with self._lock:
            if permanent:
                self._closed = True
            self._stop.set()
            process = self._process
            thread = self._thread

        self._terminate(process)
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=_STOP_JOIN_SECONDS)
            if thread.is_alive():
                with self._lock:
                    process = self._process
                self._kill(process)
                thread.join(timeout=_STOP_JOIN_SECONDS)
        with self._lock:
            if not permanent:
                if thread is not None and thread.is_alive():
                    # Never overlap a new session with a watcher whose shutdown
                    # could not be proved.
                    self._closed = True
                else:
                    self._thread = None
                    self._process = None
                    self._diagnostic_sent = False
                    self._stop.clear()

    def _watch(self, environment: dict[str, str], waiter: str) -> None:
        project_root = Path(environment["RT_PROJECT_ROOT"]).expanduser()
        agent = environment["RT_FROM"]

        while not self._stop.is_set():
            try:
                process = subprocess.Popen(
                    [waiter, agent],
                    cwd=str(project_root),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except (OSError, ValueError):
                self._fail(_CONFIG_MESSAGE)
                return

            with self._lock:
                if self._stop.is_set():
                    self._terminate(process)
                    return
                self._process = process

            try:
                output, _unused_stderr = process.communicate()
            except (OSError, ValueError):
                if not self._stop.is_set():
                    self._fail(_CONFIG_MESSAGE)
                return
            finally:
                with self._lock:
                    if self._process is process:
                        self._process = None

            if self._stop.is_set():
                return

            output = output or ""
            if process.returncode == 0 and _MAIL_MARKER in output:
                if not self._inject(_MAIL_MESSAGE):
                    self._fail(_CONFIG_MESSAGE)
                    return
                while (
                    not self._stop.is_set()
                    and _pending_non_ack_mail(project_root, agent)
                ):
                    self._stop.wait(_MAIL_DRAIN_POLL_SECONDS)
                continue

            if process.returncode == 0 and _HEARTBEAT_MARKER in output:
                continue

            if _SUPERSEDED_MARKER in output:
                self._fail(_FENCE_MESSAGE)
                return

            self._fail(_CONFIG_MESSAGE)
            return

    def _inject(self, message: str) -> bool:
        try:
            return self._ctx.inject_message(message, role="user") is True
        except Exception:
            # The plugin must never bring down Hermes if CLI injection becomes
            # unavailable during shutdown or in a non-CLI host.
            return False

    def _inject_diagnostic(self, message: str) -> None:
        with self._lock:
            if self._diagnostic_sent:
                return
            self._diagnostic_sent = True
        self._inject(message)

    def _fail(self, message: str) -> None:
        with self._lock:
            self._closed = True
            self._stop.set()
        self._inject_diagnostic(message)

    @staticmethod
    def _terminate(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
        except (OSError, ProcessLookupError, ValueError):
            pass

    @staticmethod
    def _kill(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
        except (OSError, ProcessLookupError, ValueError):
            pass


def register(ctx: Any) -> None:
    """Register lifecycle hooks without activating outside an RT session."""

    bridge = _RoundtableBridge(ctx)
    ctx.register_hook("on_session_start", bridge.on_session_start)
    ctx.register_hook("on_session_finalize", bridge.on_session_finalize)
    atexit.register(bridge.close)
