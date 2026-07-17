import base64
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import threading
from argparse import Namespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))

import _rtcodex


def load_wake_module():
    name = "rt_codex_wake_test_module"
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / "rt-codex-wake"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


wake = load_wake_module()


def load_daemon_module():
    name = "rt_codex_daemon_test_module"
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / "rt-codex-daemon"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


daemon = load_daemon_module()


def load_doctor_module():
    name = "rt_doctor_test_module"
    loader = importlib.machinery.SourceFileLoader(name, str(BIN / "rt-doctor"))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


doctor = load_doctor_module()


@pytest.fixture(autouse=True)
def isolate_wake_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(wake, "RUNTIME_DIR", tmp_path / "runtime")


def write_project(path: Path) -> Path:
    state = path / ".roundtable"
    state.mkdir(parents=True)
    (state / "agents.yaml").write_text(
        f"""schema: roundtable.agents.v1
project: {path.resolve()}
agents:
  codex:
    harness: codex
    instances:
      - id: codex
  claude:
    harness: claude-code
    instances:
      - id: claude
"""
    )
    return path.resolve()


def add_mail(project: Path, msg_id: str) -> Path:
    inbox = project / ".roundtable" / "inbox" / "codex" / "new"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{msg_id}.md"
    path.write_text(f"[CLAUDE→CODEX directive id={msg_id}] test")
    return path


def thread(project: Path, status: str = "idle", thread_id: str = "thread-1") -> dict:
    return {
        "id": thread_id,
        "sessionId": "session-1",
        "cwd": str(project),
        "source": "cli",
        "parentThreadId": None,
        "ephemeral": False,
        "status": {"type": status},
    }


class FakeClient:
    def __init__(self, value: dict):
        self.value = value
        self.calls = []
        self.turn_count = 0

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/loaded/list":
            return {"data": [self.value["id"]]}
        if method == "thread/read":
            return {"thread": dict(self.value)}
        if method == "thread/resume":
            return {"thread": dict(self.value)}
        if method == "thread/turns/list":
            return {"data": list(self.value.get("turns") or [])}
        if method == "turn/start":
            self.turn_count += 1
            return {"turn": {"id": f"turn-{self.turn_count}"}}
        raise AssertionError(method)


def test_idle_three_messages_produce_one_wake(tmp_path):
    project = write_project(tmp_path / "project")
    for index in range(3):
        add_mail(project, f"20260716T04000{index}Z-claude-to-codex-{index}")
    client = FakeClient(thread(project))
    store = wake.StateStore(tmp_path / "state.json")
    bridge = wake.WakeBridge(client, [project], store)

    first = bridge.step()
    client.value["status"] = {"type": "active"}
    second = bridge.step()

    starts = [call for call in client.calls if call[0] == "turn/start"]
    assert first[0].ok and second[0].ok
    assert len(starts) == 1
    assert "drain inbox at" in starts[0][1]["input"][0]["text"]
    assert len(list((project / ".roundtable/inbox/codex/new").iterdir())) == 3


def test_busy_waits_for_matching_turn_completed(tmp_path):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T041000Z-claude-to-codex-1")
    live_thread = thread(project, status="active")
    client = FakeClient(live_thread)
    store = wake.StateStore(tmp_path / "state.json")
    bridge = wake.WakeBridge(client, [project], store)

    assert bridge.step()[0].detail == "waiting for turn/completed"
    live_thread["status"] = {"type": "idle"}
    other = {"method": "turn/completed", "params": {"threadId": "other"}}
    assert "waiting" in bridge.step([other])[0].detail
    target = {"method": "turn/completed", "params": {"threadId": "thread-1"}}
    assert bridge.step([target])[0].detail == "wake started"

    starts = [call for call in client.calls if call[0] == "turn/start"]
    assert len(starts) == 1


def test_fresh_zero_turn_tui_uses_status_transition_when_resume_has_no_rollout(
    tmp_path,
):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T041100Z-claude-to-codex-1")
    live_thread = thread(project, status="active")

    class FreshClient(FakeClient):
        def request(self, method, params):
            if method == "thread/resume":
                self.calls.append((method, params))
                raise _rtcodex.RpcError(
                    f"thread/resume failed (-32600): no rollout found for thread id {params['threadId']}"
                )
            return super().request(method, params)

    client = FreshClient(live_thread)
    bridge = wake.WakeBridge(
        client, [project], wake.StateStore(tmp_path / "state.json")
    )

    assert bridge.step()[0].detail == "waiting for turn/completed"
    live_thread["status"] = {"type": "idle"}
    assert bridge.step()[0].detail == "wake started"
    assert client.turn_count == 1
    assert "thread-1" not in bridge.subscribed_threads


def test_failed_wake_turn_keeps_mail_and_retries_with_backoff(tmp_path):
    project = write_project(tmp_path / "project")
    mail = add_mail(project, "20260716T041500Z-claude-to-codex-1")
    client = FakeClient(thread(project))
    store = wake.StateStore(tmp_path / "state.json")
    bridge = wake.WakeBridge(client, [project], store)
    assert bridge.step()[0].detail == "wake started"

    failed = {
        "method": "turn/completed",
        "params": {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "failed"},
        },
    }
    result = bridge.step([failed])[0]

    assert not result.ok and "retry in 30s" in result.detail
    assert mail.exists()
    assert client.turn_count == 1
    state = store.project_state(project)
    state["retryAt"] = 0
    store.save()
    assert bridge.step()[0].detail == "wake started"
    assert client.turn_count == 2


def test_daemon_reconnect_resumes_before_retrying_undrained_wake(tmp_path):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T041700Z-claude-to-codex-1")
    first_client = FakeClient(thread(project))
    state_path = tmp_path / "state.json"
    store = wake.StateStore(state_path)
    assert wake.WakeBridge(first_client, [project], store).step()[0].detail == "wake started"

    recovered = thread(project)
    recovered["turns"] = [{"id": "turn-1", "status": "interrupted"}]
    second_client = FakeClient(recovered)
    second_bridge = wake.WakeBridge(
        second_client, [project], wake.StateStore(state_path)
    )

    result = second_bridge.step()[0]

    methods = [method for method, _params in second_client.calls]
    assert methods[:2] == ["thread/read", "thread/resume"]
    assert not result.ok and "retry in 30s" in result.detail
    assert "turn/start" not in methods


def test_resume_failure_obeys_persisted_backoff_on_same_connection(tmp_path):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T041750Z-claude-to-codex-1")
    store = wake.StateStore(tmp_path / "state.json")
    store.bind(project, thread(project))

    class ResumeFailClient(FakeClient):
        resume_count = 0

        def request(self, method, params):
            if method == "thread/resume":
                self.calls.append((method, params))
                self.resume_count += 1
                raise _rtcodex.RpcError("resume unavailable")
            return super().request(method, params)

    client = ResumeFailClient(thread(project))
    bridge = wake.WakeBridge(client, [project], store)

    first = bridge.step()[0]
    second = bridge.step()[0]

    assert not first.ok and "resume unavailable" in first.detail
    assert second.detail == "backoff"
    assert client.resume_count == 1


def test_same_connection_unload_resumes_active_wake_before_history_check(tmp_path):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T041800Z-claude-to-codex-1")

    class RestartingClient(FakeClient):
        unloaded = False
        resumed_after_unload = 0

        def request(self, method, params):
            if self.unloaded and method == "thread/read":
                self.calls.append((method, params))
                return {"thread": thread(project, status="notLoaded")}
            if self.unloaded and method == "thread/resume":
                self.calls.append((method, params))
                self.resumed_after_unload += 1
                return {"thread": thread(project, status="idle")}
            if self.unloaded and method == "thread/turns/list":
                self.calls.append((method, params))
                return {"data": [{"id": "turn-1", "status": "failed"}]}
            return super().request(method, params)

    client = RestartingClient(thread(project))
    bridge = wake.WakeBridge(
        client, [project], wake.StateStore(tmp_path / "state.json")
    )
    assert bridge.step()[0].detail == "wake started"
    client.unloaded = True

    result = bridge.step()[0]

    assert client.resumed_after_unload == 1
    assert not result.ok and "retry in 30s" in result.detail


def test_wrong_thread_identity_fails_closed_and_keeps_mail(tmp_path):
    project = write_project(tmp_path / "project")
    mail = add_mail(project, "20260716T042000Z-claude-to-codex-1")
    wrong = thread(tmp_path / "other")
    client = FakeClient(wrong)
    store = wake.StateStore(tmp_path / "state.json")

    result = wake.WakeBridge(client, [project], store).step()[0]

    assert not result.ok
    assert "exactly one" in result.detail
    assert mail.exists()
    assert not any(call[0] == "turn/start" for call in client.calls)


def test_remote_tui_vscode_source_binds_with_absent_thread_source(tmp_path):
    project = write_project(tmp_path / "project")
    remote = thread(project)
    remote.update({"source": "vscode", "threadSource": "user"})

    assert wake.validate_thread(project, remote)["id"] == "thread-1"

    remote["threadSource"] = "subAgent"
    with pytest.raises(wake.IdentityError, match="remote TUI threadSource is not user"):
        wake.validate_thread(project, remote)

    remote["threadSource"] = None
    validated = wake.validate_thread(project, remote, expected_id="thread-1")
    store = wake.StateStore(tmp_path / "state.json")
    store.bind(project, validated)
    assert store.bindings[str(project)]["threadId"] == "thread-1"

    remote["ephemeral"] = True
    with pytest.raises(wake.IdentityError, match="refusing to bind an ephemeral thread"):
        wake.validate_thread(project, remote, expected_id="thread-1")
    remote["ephemeral"] = False

    remote.update({"source": {"subAgent": "child"}, "threadSource": "user"})
    with pytest.raises(wake.IdentityError, match="source is not a supported TUI"):
        wake.validate_thread(project, remote)


def test_remote_vscode_thread_requires_explicit_binding_for_discovery(tmp_path):
    project = write_project(tmp_path / "project")
    remote = thread(project)
    remote.update({"source": "vscode", "threadSource": "user"})
    client = FakeClient(remote)

    with pytest.raises(wake.IdentityError, match="auto-discoverable local CLI"):
        wake.discover_thread(client, project)

    store = wake.StateStore(tmp_path / "state.json")
    store.bind(project, remote)
    selected = wake.WakeBridge(client, [project], store)._thread_for(project)
    assert selected["id"] == remote["id"]


def test_discovery_does_not_claim_uniqueness_when_a_thread_read_fails(tmp_path):
    project = write_project(tmp_path / "project")

    class PartialReadClient:
        def request(self, method, params):
            if method == "thread/loaded/list":
                return {"data": ["thread-1", "thread-2"]}
            if method == "thread/read" and params["threadId"] == "thread-1":
                return {"thread": thread(project, thread_id="thread-1")}
            if method == "thread/read":
                raise _rtcodex.RpcError("temporary read failure")
            raise AssertionError(method)

    with pytest.raises(_rtcodex.RpcError, match="temporary read failure"):
        wake.discover_thread(PartialReadClient(), project)


def test_malformed_or_symlink_mail_fails_closed(tmp_path):
    project = write_project(tmp_path / "project")
    inbox = project / ".roundtable/inbox/codex/new"
    inbox.mkdir(parents=True)
    target = tmp_path / "outside.md"
    target.write_text("outside")
    (inbox / "bad.md").symlink_to(target)

    try:
        wake.pending_generation(project)
    except wake.IdentityError as error:
        assert "non-regular" in str(error)
    else:
        raise AssertionError("symlink inbox entry was accepted")
    assert target.read_text() == "outside"


def test_malformed_project_backs_off_without_blocking_other_project(
    tmp_path, monkeypatch
):
    bad = write_project(tmp_path / "bad")
    good = write_project(tmp_path / "good")
    bad_id = "20260716T042100Z-claude-to-codex-bad"
    bad_mail = add_mail(bad, bad_id)
    bad_mail.write_bytes(b"\xff]\n")
    add_mail(good, "20260716T042100Z-claude-to-codex-good")
    original = wake.pending_generation
    calls = {str(bad): 0, str(good): 0}

    def counted(project):
        calls[str(project)] += 1
        return original(project)

    monkeypatch.setattr(wake, "pending_generation", counted)
    store = wake.StateStore(tmp_path / "state.json")
    bridge = wake.WakeBridge(FakeClient(thread(good)), [bad, good], store)

    first = bridge.step()
    second = bridge.step()

    assert not first[0].ok and "UTF-8" in first[0].detail
    assert first[1].detail == "wake started"
    assert second[0].detail == "backoff"
    assert calls[str(bad)] == 1
    assert bridge.client.turn_count == 1


def test_pending_generation_tolerates_mail_moved_during_scan(tmp_path, monkeypatch):
    project = write_project(tmp_path / "project")
    vanished = add_mail(project, "20260716T042200Z-claude-to-codex-gone")
    kept_id = "20260716T042200Z-claude-to-codex-kept"
    add_mail(project, kept_id)
    original_lstat = Path.lstat

    def moving_lstat(path):
        if path == vanished:
            path.unlink()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", moving_lstat)

    generation, ids = wake.pending_generation(project)

    assert generation is not None
    assert ids == [kept_id]


def test_long_single_line_mail_body_does_not_count_as_oversized_header(tmp_path):
    project = write_project(tmp_path / "project")
    msg_id = "20260716T042300Z-claude-to-codex-long"
    mail = add_mail(project, msg_id)
    mail.write_text(
        f"[CLAUDE→CODEX directive id={msg_id}] " + ("x" * (32 * 1024))
    )

    generation, ids = wake.pending_generation(project)

    assert generation is not None
    assert ids == [msg_id]


def test_quiet_ack_files_do_not_wake_or_change_normal_generation(tmp_path):
    project = write_project(tmp_path / "project")
    ack_id = "20260716T042400Z-claude-to-codex-ack"
    ack = add_mail(project, ack_id)
    ack.rename(ack.with_name(f"ack-{ack.name}"))

    assert wake.pending_generation(project) == (None, [])

    normal_id = "20260716T042401Z-claude-to-codex-normal"
    add_mail(project, normal_id)
    generation, ids = wake.pending_generation(project)

    assert generation is not None
    assert ids == [normal_id]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema": wake.STATE_SCHEMA, "bindings": [], "projects": {}},
        {"schema": wake.STATE_SCHEMA, "bindings": {}, "projects": []},
        {"schema": wake.STATE_SCHEMA, "bindings": {"p": []}, "projects": {}},
        {"schema": wake.STATE_SCHEMA, "bindings": {}, "projects": {"p": []}},
    ],
)
def test_corrupt_state_shape_falls_back_to_empty(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload))

    store = wake.StateStore(path)

    assert store.bindings == {}
    assert store.data["projects"] == {}


def test_invalid_retry_numeric_fields_do_not_crash_state_machine(tmp_path):
    project = write_project(tmp_path / "project")
    add_mail(project, "20260716T042350Z-claude-to-codex-1")
    store = wake.StateStore(tmp_path / "state.json")
    store.bind(project, thread(project))
    generation, _ids = wake.pending_generation(project)
    store.project_state(project).update(
        {
            "phase": "WAKE_ACTIVE",
            "retryAt": "not-a-number",
            "retryCount": "also-not-a-number",
            "lastWakeGeneration": generation,
            "lastWakeTurnId": "turn-old",
            "lastWakeThreadId": "thread-1",
        }
    )
    store.save()
    value = thread(project)
    value["turns"] = [{"id": "turn-old", "status": "failed"}]

    result = wake.WakeBridge(FakeClient(value), [project], store).step()[0]

    assert not result.ok and "retry in 30s" in result.detail
    assert store.project_state(project)["retryCount"] == 1


def test_invalid_persisted_project_disables_daemon_without_restart_storm(tmp_path):
    args = Namespace(
        project=[str(tmp_path / "missing")],
        once=False,
    )

    assert wake.run_command(args) == 0

    args.once = True
    assert wake.run_command(args) == 1


def _recv_http(conn):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        data.extend(conn.recv(4096))
    return bytes(data)


def _recv_client_frame(conn):
    first, second = conn.recv(2)
    assert first & 0x0F in (0x1, 0x8)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", conn.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", conn.recv(8))[0]
    assert second & 0x80
    mask = conn.recv(4)
    payload = bytearray()
    while len(payload) < length:
        payload.extend(conn.recv(length - len(payload)))
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def _send_server_json(conn, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) < 126:
        header = bytes((0x81, len(raw)))
    else:
        header = bytes((0x81, 126)) + struct.pack("!H", len(raw))
    conn.sendall(header + raw)


def test_real_unix_websocket_initialize_envelope(tmp_path):
    # Darwin sockaddr_un paths are limited to roughly 104 bytes; pytest's
    # default temp path is longer than that on this machine.
    path = Path("/private/tmp") / f"rtws-{os.getpid()}-{tmp_path.name[-6:]}.sock"
    path.unlink(missing_ok=True)
    ready = threading.Event()
    observed = []

    def server():
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        ready.set()
        conn, _ = listener.accept()
        header = _recv_http(conn).decode("latin1")
        key_line = next(
            line
            for line in header.split("\r\n")
            if line.lower().startswith("sec-websocket-key:")
        )
        key = key_line.split(":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        conn.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        initialize = json.loads(_recv_client_frame(conn))
        observed.append(initialize)
        _send_server_json(conn, {"id": initialize["id"], "result": {"codexHome": "/tmp"}})
        observed.append(json.loads(_recv_client_frame(conn)))
        conn.close()
        listener.close()

    worker = threading.Thread(target=server)
    worker.start()
    assert ready.wait(2)
    client = _rtcodex.AppServerClient(path)
    try:
        assert observed[0]["method"] == "initialize"
    finally:
        client.close()
    worker.join(2)
    path.unlink(missing_ok=True)

    assert "jsonrpc" not in observed[0]
    assert observed[0]["params"]["clientInfo"]["name"] == "roundtable_rt_codex_wake"
    assert observed[0]["params"]["capabilities"] == {"experimentalApi": True}
    assert observed[1] == {"method": "initialized"}


@pytest.mark.parametrize("payload", [b"{", b"[]", b"\xff"])
def test_websocket_rejects_malformed_or_non_object_json(payload):
    class FakeSocket:
        timeout = 1.0

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

    transport = object.__new__(_rtcodex.WebSocketUnix)
    transport.sock = FakeSocket()
    transport._read_frame = lambda: (True, 0x1, payload)

    with pytest.raises(_rtcodex.CodexRuntimeError, match="JSON message"):
        transport.recv_json()


def test_rpc_non_object_error_and_result_fail_safely():
    class FakeTransport:
        responses = [
            {"id": 1, "error": ["bad"]},
            {"id": 2, "result": ["bad"]},
        ]

        def send_json(self, _payload):
            pass

        def recv_json(self, _timeout):
            return self.responses.pop(0)

    client = object.__new__(_rtcodex.AppServerClient)
    client.transport = FakeTransport()
    client.timeout = 1.0
    client.next_id = 1
    client.notifications = []

    with pytest.raises(_rtcodex.RpcError, match=r"\['bad'\]"):
        client.request("first")
    with pytest.raises(_rtcodex.CodexRuntimeError, match="expected an object"):
        client.request("second")


def test_ensure_daemon_kickstarts_once(monkeypatch, tmp_path):
    socket_path = tmp_path / "app.sock"
    refused = ConnectionRefusedError("down")
    outcomes = iter(
        [
            (False, "down", refused),
            (False, "down", refused),
            (True, "ready", None),
        ]
    )
    calls = []
    monkeypatch.setattr(_rtcodex, "DEFAULT_SOCKET", socket_path)
    monkeypatch.setattr(_rtcodex, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        _rtcodex, "probe_handshake_detailed", lambda *args, **kwargs: next(outcomes)
    )
    monkeypatch.setattr(_rtcodex, "app_server_plist", lambda path: {"Label": "test"})
    monkeypatch.setattr(
        _rtcodex,
        "install_launch_agent",
        lambda *args, **kwargs: calls.append("install"),
    )
    monkeypatch.setattr(
        _rtcodex, "kickstart", lambda *args, **kwargs: calls.append("kickstart")
    )

    _rtcodex.ensure_daemon(socket_path, timeout=2)

    assert calls == ["install", "kickstart"]
    lock_path = tmp_path / "runtime/codex-app-server-start.lock"
    assert lock_path.is_file()
    with lock_path.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_ensure_daemon_refuses_restart_on_permission_error(monkeypatch, tmp_path):
    socket_path = tmp_path / "app.sock"
    socket_path.touch()
    calls = []
    monkeypatch.setattr(_rtcodex, "DEFAULT_SOCKET", socket_path)
    monkeypatch.setattr(_rtcodex, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(
        _rtcodex,
        "probe_handshake_detailed",
        lambda *args, **kwargs: (False, "operation not permitted", PermissionError(1)),
    )
    monkeypatch.setattr(
        _rtcodex,
        "install_launch_agent",
        lambda *args, **kwargs: calls.append("install"),
    )
    monkeypatch.setattr(
        _rtcodex, "kickstart", lambda *args, **kwargs: calls.append("kickstart")
    )

    with pytest.raises(_rtcodex.CodexRuntimeError, match="refusing daemon restart"):
        _rtcodex.ensure_daemon(socket_path)
    assert calls == []


def test_ensure_daemon_rejects_custom_socket_before_any_side_effect(monkeypatch, tmp_path):
    calls = []
    custom = tmp_path / "custom.sock"
    monkeypatch.setattr(
        _rtcodex,
        "probe_handshake_detailed",
        lambda *args, **kwargs: calls.append("probe"),
    )
    monkeypatch.setattr(
        _rtcodex,
        "install_launch_agent",
        lambda *args, **kwargs: calls.append("install"),
    )
    monkeypatch.setattr(
        _rtcodex, "kickstart", lambda *args, **kwargs: calls.append("kickstart")
    )

    with pytest.raises(_rtcodex.UnsupportedVersion, match="non-default"):
        _rtcodex.ensure_daemon(custom)
    assert calls == []


def test_daemon_install_rejects_custom_socket_before_launchctl(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(
        daemon,
        "install_launch_agent",
        lambda *args, **kwargs: calls.append("install"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["rt-codex-daemon", "install", "--socket", str(tmp_path / "custom.sock"), "--reload"],
    )

    assert daemon.main() == 1
    assert calls == []
    assert "non-default app-server socket" in capsys.readouterr().err


def test_wake_install_rejects_custom_socket_before_plist_or_launchctl(
    monkeypatch, tmp_path, capsys
):
    calls = []
    monkeypatch.setattr(
        wake,
        "wake_plist",
        lambda *args, **kwargs: calls.append("plist"),
    )
    monkeypatch.setattr(
        wake,
        "install_launch_agent",
        lambda *args, **kwargs: calls.append("install"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rt-codex-wake",
            "--socket",
            str(tmp_path / "custom.sock"),
            "install",
            "--reload",
        ],
    )

    assert wake.main() == 1
    assert calls == []
    assert "non-default app-server socket" in capsys.readouterr().err


def test_singleton_kernel_lock_releases_without_unlink(tmp_path):
    path = tmp_path / "bridge.lock"
    first = wake.acquire_singleton(path)
    with pytest.raises(_rtcodex.CodexRuntimeError):
        wake.acquire_singleton(path)
    first.close()  # Kernel releases the lock; the file intentionally remains.
    second = wake.acquire_singleton(path)
    second.close()
    assert path.is_file()


def test_running_store_does_not_overwrite_external_rebind(tmp_path):
    project = write_project(tmp_path / "project")
    path = tmp_path / "state.json"
    running = wake.StateStore(path)
    running.bind(project, thread(project, thread_id="thread-old"))
    external = wake.StateStore(path)
    external.bind(project, thread(project, thread_id="thread-new"))

    running.project_state(project).update(
        {
            "phase": "WAKE_ACTIVE",
            "lastWakeGeneration": "stale-generation",
            "lastWakeTurnId": "stale-turn",
        }
    )
    running.save()

    reloaded = wake.StateStore(path)
    assert reloaded.bindings[str(project)]["threadId"] == "thread-new"
    assert reloaded.project_state(project)["phase"] == "EMPTY"
    assert "reboundAt" in reloaded.project_state(project)
    assert "lastWakeGeneration" not in reloaded.project_state(project)


def test_resume_excludes_turn_history(tmp_path):
    project = write_project(tmp_path / "project")
    client = FakeClient(thread(project))

    resumed = wake.resume_thread(client, project, "thread-1")

    assert resumed["id"] == "thread-1"
    assert client.calls[-1] == (
        "thread/resume",
        {"threadId": "thread-1", "excludeTurns": True},
    )


def test_same_connection_not_loaded_thread_is_resumed(tmp_path):
    project = write_project(tmp_path / "project")

    class UnloadedClient(FakeClient):
        def request(self, method, params):
            self.calls.append((method, params))
            if method == "thread/read":
                return {"thread": thread(project, status="notLoaded")}
            if method == "thread/resume":
                return {"thread": thread(project, status="idle")}
            raise AssertionError(method)

    client = UnloadedClient(thread(project))
    store = wake.StateStore(tmp_path / "state.json")
    store.bind(project, thread(project))
    bridge = wake.WakeBridge(client, [project], store)

    refreshed = bridge._refresh(project, "thread-1")

    assert wake.status_type(refreshed) == "idle"
    assert [method for method, _params in client.calls] == [
        "thread/read",
        "thread/read",
        "thread/resume",
    ]


def test_rebind_before_locked_start_prevents_old_thread_wake(tmp_path):
    project = write_project(tmp_path / "project")
    state_path = tmp_path / "state.json"
    store = wake.StateStore(state_path)
    store.bind(project, thread(project, thread_id="thread-old"))
    external = wake.StateStore(state_path)

    class RebindingClient(FakeClient):
        def request(self, method, params):
            if method == "thread/read":
                external.bind(project, thread(project, thread_id="thread-new"))
            return super().request(method, params)

    client = RebindingClient(thread(project, thread_id="thread-old"))
    bridge = wake.WakeBridge(client, [project], store)

    with pytest.raises(wake.IdentityError, match="binding changed before wake"):
        bridge._wake(project, "thread-old", "generation", 1)

    assert not any(method == "turn/start" for method, _params in client.calls)
    assert wake.StateStore(state_path).bindings[str(project)]["threadId"] == "thread-new"


def test_auto_discovery_does_not_overwrite_concurrent_explicit_bind(tmp_path):
    project = write_project(tmp_path / "project")
    state_path = tmp_path / "state.json"
    store = wake.StateStore(state_path)
    external = wake.StateStore(state_path)

    class DiscoverRaceClient:
        def __init__(self):
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            if method == "thread/loaded/list":
                external.bind(project, thread(project, thread_id="thread-new"))
                return {"data": ["thread-old"]}
            if method == "thread/read":
                return {
                    "thread": thread(project, thread_id=params["threadId"])
                }
            if method == "thread/resume":
                return {
                    "thread": thread(project, thread_id=params["threadId"])
                }
            raise AssertionError(method)

    bridge = wake.WakeBridge(DiscoverRaceClient(), [project], store)

    selected = bridge._thread_for(project)

    assert selected["id"] == "thread-new"
    persisted = wake.StateStore(state_path)
    assert persisted.bindings[str(project)]["threadId"] == "thread-new"


def test_rebind_during_start_waits_then_resets_wake_state(tmp_path):
    project = write_project(tmp_path / "project")
    state_path = tmp_path / "state.json"
    store = wake.StateStore(state_path)
    store.bind(project, thread(project, thread_id="thread-old"))
    external = wake.StateStore(state_path)

    class ConcurrentRebindClient(FakeClient):
        worker = None
        attempted = threading.Event()
        finished = threading.Event()

        def request(self, method, params):
            if method == "turn/start":
                def rebind():
                    self.attempted.set()
                    external.bind(project, thread(project, thread_id="thread-new"))
                    self.finished.set()

                self.worker = threading.Thread(target=rebind)
                self.worker.start()
                assert self.attempted.wait(1)
            return super().request(method, params)

    client = ConcurrentRebindClient(thread(project, thread_id="thread-old"))
    bridge = wake.WakeBridge(client, [project], store)

    assert bridge._wake(project, "thread-old", "generation", 1)
    client.worker.join(2)

    assert client.finished.is_set()
    persisted = wake.StateStore(state_path)
    assert persisted.bindings[str(project)]["threadId"] == "thread-new"
    assert persisted.project_state(project)["phase"] == "EMPTY"
    assert "lastWakeGeneration" not in persisted.project_state(project)


def test_version_allowlist_is_exact():
    assert _rtcodex.version_is_validated((0, 144, 3))
    assert not _rtcodex.version_is_validated((0, 144, 2))
    assert not _rtcodex.version_is_validated((0, 144, 4))


def test_rt_codex_socket_environment_cannot_redefine_validated_default(tmp_path):
    env = os.environ.copy()
    env["RT_CODEX_SOCKET"] = str(tmp_path / "unvalidated.sock")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, 'bin'); "
                "import _rtcodex; print(_rtcodex.DEFAULT_SOCKET)"
            ),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(
        Path.home() / ".codex/app-server-control/app-server-control.sock"
    )
    assert proc.stdout.strip() != env["RT_CODEX_SOCKET"]


def test_daemon_version_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(
        _rtcodex, "codex_version", lambda: ((0, 144, 3), "codex-cli 0.144.3")
    )
    monkeypatch.setattr(
        _rtcodex,
        "daemon_version",
        lambda _path: (
            {
                "status": "running",
                "socketPath": str(_rtcodex.DEFAULT_SOCKET),
                "appServerVersion": "0.144.4",
            },
            "",
        ),
    )

    with pytest.raises(_rtcodex.CodexRuntimeError, match="version mismatch"):
        _rtcodex.require_validated_daemon()


def test_launchd_payloads_are_persistent_and_explicit(tmp_path, monkeypatch):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\n")
    fake_codex.chmod(0o755)
    monkeypatch.setenv("RT_CODEX_BIN", str(fake_codex))
    monkeypatch.setattr(_rtcodex, "RUNTIME_DIR", tmp_path / "runtime")

    app = _rtcodex.app_server_plist(tmp_path / "app.sock")
    bridge = _rtcodex.wake_plist(tmp_path / "app.sock")

    assert app["RunAtLoad"] and app["KeepAlive"]
    assert app["ProgramArguments"][-2:] == ["--listen", f"unix://{tmp_path / 'app.sock'}"]
    assert bridge["RunAtLoad"] and bridge["KeepAlive"] == {"SuccessfulExit": False}
    assert bridge["ProgramArguments"][-1] == "run"


def test_launchd_payload_persists_configured_projects(tmp_path, monkeypatch):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\n")
    fake_codex.chmod(0o755)
    project = tmp_path / "outside-rl"
    project.mkdir()
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setenv("RT_CODEX_BIN", str(fake_codex))
    monkeypatch.setattr(_rtcodex, "RUNTIME_DIR", tmp_path / "runtime")

    payload = _rtcodex.wake_plist(
        tmp_path / "app.sock",
        projects=[str(project)],
        fallback_project=str(fallback),
    )

    assert payload["ProgramArguments"][-2:] == ["--project", str(project)]
    assert payload["EnvironmentVariables"]["RT_FALLBACK_PROJECT"] == str(fallback)


def test_explicit_launch_agent_reload_applies_unchanged_plist(tmp_path, monkeypatch):
    path = tmp_path / "agent.plist"
    commands = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(_rtcodex, "launch_agent_path", lambda _label: path)
    monkeypatch.setattr(_rtcodex, "_write_plist", lambda _path, _payload: False)
    monkeypatch.setattr(_rtcodex, "launchd_loaded", lambda _label: True)
    monkeypatch.setattr(
        _rtcodex.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or Result(),
    )

    _rtcodex.install_launch_agent("test.agent", {"Label": "test.agent"}, reload=True)

    assert commands[0][1] == "bootout"
    assert commands[1][1] == "bootstrap"


def test_doctor_failure_matrix_and_install_fix(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "codex_version", lambda: ((0, 144, 3), "codex-cli 0.144.3"))
    monkeypatch.setattr(doctor, "daemon_version", lambda _path: (None, "down"))
    monkeypatch.setattr(doctor, "socket_check", lambda _path: (False, "missing"))
    monkeypatch.setattr(doctor, "probe_handshake", lambda _path: (False, "refused"))
    monkeypatch.setattr(doctor, "bridge_check", lambda *_args: (False, "missing pid"))
    monkeypatch.setattr(doctor, "launchd_loaded", lambda _label: False)
    monkeypatch.setattr(sys, "argv", ["rt-doctor"])

    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert "FAIL daemon:" in output
    assert "FAIL socket:" in output
    assert "FAIL rpc:" in output
    assert "OK version:" in output
    assert "FAIL bridge:" in output
    assert "~/.roundtable/bin/rt-codex-wake install" in output


def test_doctor_unsupported_version_warns_and_exits_zero(monkeypatch, capsys):
    socket_path = doctor.DEFAULT_SOCKET
    monkeypatch.setattr(doctor, "codex_version", lambda: ((0, 144, 4), "codex-cli 0.144.4"))
    monkeypatch.setattr(
        doctor,
        "daemon_version",
        lambda _path: (
            {
                "status": "running",
                "socketPath": str(socket_path),
                "appServerVersion": "0.144.4",
            },
            "",
        ),
    )
    monkeypatch.setattr(doctor, "socket_check", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "probe_handshake", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "bridge_check", lambda *_args: (False, "disabled"))
    monkeypatch.setattr(doctor, "launchd_loaded", lambda _label: False)
    monkeypatch.setattr(sys, "argv", ["rt-doctor"])

    assert doctor.main() == 0
    output = capsys.readouterr().out
    assert "WARN version:" in output
    assert "WARN bridge:" in output


def test_doctor_rejects_daemon_on_different_socket(monkeypatch, capsys, tmp_path):
    requested = tmp_path / "requested.sock"
    monkeypatch.setattr(doctor, "codex_version", lambda: ((0, 144, 3), "codex-cli 0.144.3"))
    monkeypatch.setattr(
        doctor,
        "daemon_version",
        lambda _path: (
            {
                "status": "running",
                "socketPath": "/tmp/other.sock",
                "appServerVersion": "0.144.3",
            },
            "",
        ),
    )
    monkeypatch.setattr(doctor, "socket_check", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "probe_handshake", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "bridge_check", lambda *_args: (True, "ok"))
    monkeypatch.setattr(sys, "argv", ["rt-doctor", "--socket", str(requested)])

    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert "FAIL daemon: reported socket /tmp/other.sock" in output


def test_doctor_version_mismatch_reinstalls_loaded_plist(monkeypatch, capsys):
    socket_path = doctor.DEFAULT_SOCKET
    monkeypatch.setattr(
        doctor, "codex_version", lambda: ((0, 144, 3), "codex-cli 0.144.3")
    )
    monkeypatch.setattr(
        doctor,
        "daemon_version",
        lambda _path: (
            {
                "status": "running",
                "socketPath": str(socket_path),
                "appServerVersion": "0.144.2",
            },
            "",
        ),
    )
    monkeypatch.setattr(doctor, "socket_check", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "probe_handshake", lambda _path: (True, "ok"))
    monkeypatch.setattr(doctor, "bridge_check", lambda *_args: (True, "ok"))
    monkeypatch.setattr(sys, "argv", ["rt-doctor"])

    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert "FAIL version:" in output
    assert "rt-codex-daemon install --reload" in output


def test_bridge_check_reports_non_object_heartbeat_as_failure(tmp_path, monkeypatch):
    pid_path = tmp_path / "rt-codex-wake.pid"
    pid_path.write_text("123\n")
    (tmp_path / "rt-codex-wake-heartbeat.json").write_text("[]")
    monkeypatch.setattr(doctor, "pid_is_running", lambda *_args: (True, "pid 123"))

    ok, detail = doctor.bridge_check(tmp_path, 15)

    assert not ok
    assert "not a JSON object" in detail
