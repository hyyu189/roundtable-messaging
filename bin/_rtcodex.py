"""Codex app-server plumbing shared by rt-codex-wake and rt-doctor.

The local app-server control socket speaks WebSocket-over-UDS (not JSONL).
This module intentionally uses only the Python standard library so launchd
does not depend on a particular virtual environment.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import plistlib
import secrets
import select
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path


ROUND_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path(
    os.environ.get("RT_CODEX_RUNTIME_DIR", ROUND_ROOT / ".runtime")
).expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
DEFAULT_SOCKET = (CODEX_HOME / "app-server-control" / "app-server-control.sock").expanduser()
APP_SERVER_LABEL = "com.roundtable.codex-app-server"
WAKE_LABEL = "com.roundtable.codex-wake"
VALIDATED_CODEX_MIN = (0, 144, 3)
VALIDATED_CODEX_MAX = (0, 144, 3)


class CodexRuntimeError(RuntimeError):
    pass


class RpcError(CodexRuntimeError):
    pass


class UnsupportedVersion(CodexRuntimeError):
    pass


def _env_path() -> str:
    entries = [
        str(Path.home() / ".npm-global" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return ":".join(entries)


def codex_bin() -> Path:
    override = os.environ.get("RT_CODEX_BIN")
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
        raise CodexRuntimeError(f"RT_CODEX_BIN is not executable: {path}")

    candidates = [
        CODEX_HOME / "packages" / "standalone" / "current" / "codex",
        Path.home() / ".npm-global" / "bin" / "codex",
        Path.home() / ".local" / "bin" / "codex",
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()

    found = shutil.which("codex", path=_env_path())
    if found:
        return Path(found).resolve()
    raise CodexRuntimeError("could not find an executable Codex CLI")


def launchctl_bin() -> str:
    return os.environ.get("RT_LAUNCHCTL", "/bin/launchctl")


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launch_agent_path(label: str) -> Path:
    root = Path(os.environ.get("RT_LAUNCH_AGENTS_DIR", Path.home() / "Library" / "LaunchAgents"))
    return root.expanduser() / f"{label}.plist"


def app_server_plist(socket_path: Path = DEFAULT_SOCKET) -> dict:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "Label": APP_SERVER_LABEL,
        "ProgramArguments": [
            str(codex_bin()),
            "app-server",
            "--listen",
            f"unix://{socket_path}",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": _env_path(),
            "CODEX_HOME": str(CODEX_HOME),
        },
        "StandardOutPath": str(RUNTIME_DIR / "codex-app-server.stdout.log"),
        "StandardErrorPath": str(RUNTIME_DIR / "codex-app-server.stderr.log"),
    }


def wake_plist(
    socket_path: Path = DEFAULT_SOCKET,
    *,
    auto_discover: bool = False,
) -> dict:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    arguments = [
        str(ROUND_ROOT / "bin" / "rt-codex-wake"),
        "--socket",
        str(socket_path),
        "run",
    ]
    if auto_discover:
        arguments.append("--auto-discover")
    environment = {
        "HOME": str(Path.home()),
        "PATH": _env_path(),
        "CODEX_HOME": str(CODEX_HOME),
        "RT_CODEX_RUNTIME_DIR": str(RUNTIME_DIR),
    }
    if os.environ.get("RT_PROJECTS_FILE"):
        environment["RT_PROJECTS_FILE"] = str(
            Path(os.environ["RT_PROJECTS_FILE"]).expanduser().resolve()
        )
    return {
        "Label": WAKE_LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": environment,
        "StandardOutPath": str(RUNTIME_DIR / "rt-codex-wake.stdout.log"),
        "StandardErrorPath": str(RUNTIME_DIR / "rt-codex-wake.stderr.log"),
    }


def _write_plist(path: Path, payload: dict) -> bool:
    content = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return True


def launchd_loaded(label: str) -> bool:
    proc = subprocess.run(
        [launchctl_bin(), "print", f"{launch_domain()}/{label}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def launchd_running(label: str) -> bool:
    proc = subprocess.run(
        [launchctl_bin(), "print", f"{launch_domain()}/{label}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and "state = running" in proc.stdout


def install_launch_agent(label: str, payload: dict, *, reload: bool = False) -> Path:
    path = launch_agent_path(label)
    _write_plist(path, payload)
    loaded = launchd_loaded(label)
    # Explicit reload also repairs a live job whose plist was rewritten by an
    # earlier process before that process could reload it.
    if loaded and reload:
        proc = subprocess.run(
            [launchctl_bin(), "bootout", f"{launch_domain()}/{label}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise CodexRuntimeError(
                f"launchctl bootout failed for {label}: {proc.stderr.strip()}"
            )
        loaded = False
    if not loaded:
        proc = subprocess.run(
            [launchctl_bin(), "bootstrap", launch_domain(), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise CodexRuntimeError(
                f"launchctl bootstrap failed for {label}: {proc.stderr.strip()}"
            )
    return path


def kickstart(label: str, *, force: bool = True) -> None:
    command = [launchctl_bin(), "kickstart"]
    if force:
        command.append("-k")
    command.append(f"{launch_domain()}/{label}")
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CodexRuntimeError(
            f"launchctl kickstart failed for {label}: {proc.stderr.strip()}"
        )


class WebSocketUnix:
    """Small RFC 6455 client for the app-server's local Unix socket."""

    MAX_MESSAGE = 16 * 1024 * 1024

    def __init__(self, path: Path, timeout: float = 3.0):
        self.path = Path(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(self.path))
        self._upgrade()

    def _upgrade(self) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CodexRuntimeError("app-server closed during WebSocket upgrade")
            response.extend(chunk)
            if len(response) > 65536:
                raise CodexRuntimeError("oversized WebSocket upgrade response")
        header, _, extra = bytes(response).partition(b"\r\n\r\n")
        if extra:
            raise CodexRuntimeError("unexpected frame bytes in WebSocket upgrade response")
        lines = header.decode("latin1").split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise CodexRuntimeError(f"WebSocket upgrade failed: {lines[0] if lines else ''}")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise CodexRuntimeError("invalid WebSocket Sec-WebSocket-Accept")

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=0x8)
        except OSError:
            pass
        self.sock.close()

    def fileno(self) -> int:
        return self.sock.fileno()

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        mask = secrets.token_bytes(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send_json(self, payload: dict) -> None:
        self._send_frame(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            opcode=0x1,
        )

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.sock.recv(size - len(chunks))
            if not chunk:
                raise CodexRuntimeError("app-server WebSocket closed")
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > self.MAX_MESSAGE:
            raise CodexRuntimeError(f"WebSocket message exceeds {self.MAX_MESSAGE} bytes")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def recv_json(self, timeout: float | None = None) -> dict:
        previous_timeout = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            parts = []
            message_opcode = None
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == 0x8:
                    raise CodexRuntimeError("app-server closed WebSocket")
                if opcode == 0x9:
                    self._send_frame(payload, opcode=0xA)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x2):
                    message_opcode = opcode
                    parts = [payload]
                elif opcode == 0x0 and message_opcode is not None:
                    parts.append(payload)
                else:
                    raise CodexRuntimeError(f"unexpected WebSocket opcode {opcode}")
                if not fin:
                    if sum(len(part) for part in parts) > self.MAX_MESSAGE:
                        raise CodexRuntimeError(
                            f"WebSocket message exceeds {self.MAX_MESSAGE} bytes"
                        )
                    continue
                if message_opcode != 0x1:
                    raise CodexRuntimeError("expected a WebSocket text message")
                try:
                    message = json.loads(b"".join(parts).decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise CodexRuntimeError(
                        f"invalid app-server JSON message: {error}"
                    ) from error
                if not isinstance(message, dict):
                    raise CodexRuntimeError(
                        "invalid app-server JSON message: expected an object"
                    )
                return message
        finally:
            self.sock.settimeout(previous_timeout)


class AppServerClient:
    def __init__(self, path: Path = DEFAULT_SOCKET, timeout: float = 3.0):
        self.path = Path(path)
        self.transport = WebSocketUnix(self.path, timeout=timeout)
        self.timeout = timeout
        self.next_id = 1
        self.notifications: list[dict] = []
        self._initialize()

    def _initialize(self) -> None:
        self.initialize_result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "roundtable_rt_codex_wake",
                    "title": "Roundtable Codex wake bridge",
                    "version": "1.0.0",
                },
                # excludeTurns and bounded turn-history paging are
                # experimental in app-server 0.144.3.
                "capabilities": {"experimentalApi": True},
            },
        )
        self.notify("initialized")

    def close(self) -> None:
        self.transport.close()

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"method": method}
        if params is not None:
            payload["params"] = params
        self.transport.send_json(payload)

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        payload = {"method": method, "id": request_id, "params": params or {}}
        self.transport.send_json(payload)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {method}")
            message = self.transport.recv_json(remaining)
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                if "error" in message:
                    error = message["error"]
                    if isinstance(error, dict):
                        detail = f"({error.get('code')}): {error.get('message')}"
                    else:
                        detail = repr(error)
                    raise RpcError(f"{method} failed {detail}")
                result = message.get("result")
                if result is None:
                    return {}
                if not isinstance(result, dict):
                    raise CodexRuntimeError(
                        f"invalid {method} result: expected an object"
                    )
                return result
            self.notifications.append(message)

    def drain_notifications(self, timeout: float = 0.0) -> list[dict]:
        messages = self.notifications
        self.notifications = []
        wait = timeout
        while True:
            readable, _, _ = select.select([self.transport], [], [], wait)
            if not readable:
                return messages
            messages.append(self.transport.recv_json(self.timeout))
            wait = 0.0


def probe_handshake_detailed(
    socket_path: Path = DEFAULT_SOCKET, timeout: float = 1.0
) -> tuple[bool, str, Exception | None]:
    client = None
    try:
        client = AppServerClient(socket_path, timeout=timeout)
        return True, "initialize/initialized succeeded", None
    except Exception as error:
        return False, str(error), error
    finally:
        if client is not None:
            client.close()


def probe_handshake(socket_path: Path = DEFAULT_SOCKET, timeout: float = 1.0) -> tuple[bool, str]:
    ok, detail, _error = probe_handshake_detailed(socket_path, timeout)
    return ok, detail


def repairable_probe_failure(socket_path: Path, error: Exception | None) -> bool:
    if isinstance(error, PermissionError):
        return False
    if not socket_path.exists():
        return True
    return isinstance(
        error,
        (
            FileNotFoundError,
            ConnectionRefusedError,
            ConnectionResetError,
            ConnectionAbortedError,
        ),
    )


def require_default_socket(socket_path: Path) -> None:
    """Reject endpoints that would reuse the global LaunchAgent label unsafely."""
    if socket_path != DEFAULT_SOCKET:
        raise UnsupportedVersion(
            f"non-default app-server socket is not version-validated: {socket_path}"
        )


def ensure_daemon(socket_path: Path = DEFAULT_SOCKET, timeout: float = 10.0) -> None:
    """Handshake the daemon, single-flight install/kickstart it if unavailable."""
    # This must precede every probe and launchd operation. The app-server uses
    # one global label, so healing a custom endpoint would otherwise boot out
    # and rewrite the healthy default daemon first.
    require_default_socket(socket_path)
    ok, detail, error = probe_handshake_detailed(
        socket_path, timeout=min(1.0, timeout)
    )
    if ok:
        return
    if not repairable_probe_failure(socket_path, error):
        raise CodexRuntimeError(
            f"refusing daemon restart after non-liveness probe failure: {detail}"
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = RUNTIME_DIR / "codex-app-server-start.lock"
    deadline = time.monotonic() + timeout
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+")
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            ok, wait_detail, wait_error = probe_handshake_detailed(
                socket_path, timeout=0.5
            )
            if ok:
                handle.close()
                return
            if not repairable_probe_failure(socket_path, wait_error):
                handle.close()
                raise CodexRuntimeError(
                    "refusing daemon restart while another caller holds the lock "
                    f"after non-liveness probe failure: {wait_detail}"
                )
            if time.monotonic() >= deadline:
                handle.close()
                raise CodexRuntimeError(
                    "timed out waiting for app-server single-flight lock"
                )
            time.sleep(0.2)

    try:
        ok, detail, error = probe_handshake_detailed(socket_path, timeout=0.5)
        if ok:
            return
        if not repairable_probe_failure(socket_path, error):
            raise CodexRuntimeError(
                f"refusing daemon restart after non-liveness probe failure: {detail}"
            )
        install_launch_agent(
            APP_SERVER_LABEL, app_server_plist(socket_path), reload=True
        )
        kickstart(APP_SERVER_LABEL, force=False)
        last_error = "daemon did not become ready"
        while time.monotonic() < deadline:
            ok, last_error = probe_handshake(socket_path, timeout=0.5)
            if ok:
                return
            time.sleep(0.2)
        raise CodexRuntimeError(f"app-server self-heal failed: {last_error}")
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def parse_codex_version(text: str) -> tuple[int, int, int] | None:
    for token in text.strip().split():
        parts = token.split(".")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            return tuple(int(part) for part in parts)  # type: ignore[return-value]
    return None


def codex_version() -> tuple[tuple[int, int, int] | None, str]:
    path = codex_bin()
    proc = subprocess.run(
        [str(path), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={**os.environ, "PATH": _env_path()},
    )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        return None, output
    return parse_codex_version(output), output


def daemon_version(socket_path: Path = DEFAULT_SOCKET) -> tuple[dict | None, str]:
    """Ask the installed CLI to inspect the running app-server daemon."""
    proc = subprocess.run(
        [str(codex_bin()), "app-server", "daemon", "version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": _env_path(),
            "RT_CODEX_SOCKET": str(socket_path),
            "CODEX_HOME": str(CODEX_HOME),
        },
    )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        return None, output
    start = output.find("{")
    if start < 0:
        return None, output
    try:
        return json.loads(output[start:]), output
    except json.JSONDecodeError:
        return None, output


def version_is_validated(version: tuple[int, int, int]) -> bool:
    return VALIDATED_CODEX_MIN <= version <= VALIDATED_CODEX_MAX


def require_validated_version() -> tuple[int, int, int]:
    version, output = codex_version()
    if version is None:
        raise CodexRuntimeError(f"could not parse Codex version: {output}")
    if not version_is_validated(version):
        rendered = ".".join(str(part) for part in version)
        raise UnsupportedVersion(
            f"Codex {rendered} is outside the validated app-server wake range "
            "0.144.3; use legacy keyboard nudge until revalidated"
        )
    return version


def require_validated_daemon(socket_path: Path = DEFAULT_SOCKET) -> dict:
    """Fail closed when CLI and default-socket app-server versions diverge."""
    require_default_socket(socket_path)
    cli = require_validated_version()
    daemon, detail = daemon_version(socket_path)
    if not daemon or daemon.get("status") != "running":
        raise CodexRuntimeError(f"could not validate app-server version: {detail}")
    reported_socket = daemon.get("socketPath")
    if reported_socket != str(socket_path):
        raise CodexRuntimeError(
            f"daemon socket mismatch: {reported_socket!r} != {str(socket_path)!r}"
        )
    app_version = daemon.get("appServerVersion")
    cli_rendered = ".".join(str(part) for part in cli)
    if app_version != cli_rendered:
        raise CodexRuntimeError(
            f"CLI/app-server version mismatch: {cli_rendered} != {app_version}"
        )
    return daemon


def pid_is_running(pid_path: Path, expected_fragment: str) -> tuple[bool, str]:
    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return False, f"missing or invalid pid file {pid_path}"
    try:
        os.kill(pid, 0)
    except OSError as error:
        return False, f"pid {pid} is not alive: {error}"
    proc = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or expected_fragment not in proc.stdout:
        return False, f"pid {pid} does not match {expected_fragment}"
    return True, f"pid {pid}"
