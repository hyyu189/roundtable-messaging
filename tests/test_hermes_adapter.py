from __future__ import annotations

import importlib.util
from pathlib import Path
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations" / "hermes" / "roundtable" / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("roundtable_hermes_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self, *, inject_result=True, inject_error=None) -> None:
        self.hooks = {}
        self.injected: list[tuple[str, str]] = []
        self.injected_event = threading.Event()
        self.inject_result = inject_result
        self.inject_error = inject_error

    def register_hook(self, name, callback) -> None:
        self.hooks[name] = callback

    def inject_message(self, content, role="user") -> bool:
        self.injected.append((content, role))
        self.injected_event.set()
        if self.inject_error is not None:
            raise self.inject_error
        return self.inject_result


class FakeProcess:
    def __init__(self, output: str | None = None, returncode: int = 0) -> None:
        self.output = output
        self.returncode = None
        self.final_returncode = returncode
        self.communicating = threading.Event()
        self.release = threading.Event()
        self.terminated = False
        self.killed = False
        if output is not None:
            self.release.set()

    def communicate(self):
        self.communicating.set()
        assert self.release.wait(5)
        self.returncode = self.final_returncode
        return self.output or "", None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.final_returncode = -15
        self.release.set()

    def kill(self) -> None:
        self.killed = True
        self.final_returncode = -9
        self.release.set()


class FakePopen:
    def __init__(self, plans) -> None:
        self.plans = list(plans)
        self.calls = []
        self.created: list[FakeProcess] = []
        self.called = threading.Event()

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        plan = self.plans.pop(0)
        process = (
            plan
            if isinstance(plan, FakeProcess)
            else FakeProcess(*plan)
        )
        self.created.append(process)
        self.called.set()
        return process


def _set_activation(monkeypatch, tmp_path, agent="hermes"):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    prefix = tmp_path / "prefix"
    waiter = prefix / "bin" / "rt-wait-inbox"
    waiter.parent.mkdir(parents=True)
    waiter.write_text("#!/bin/sh\n", encoding="utf-8")
    waiter.chmod(0o755)
    monkeypatch.setenv("RT_PROJECT_ROOT", str(project))
    monkeypatch.setenv("RT_FROM", agent)
    monkeypatch.setenv("RT_SESSION_ID", "session-1")
    monkeypatch.setenv("RT_LEASE_REVISION", "revision-1")
    monkeypatch.setenv("ROUNDTABLE_INSTALL_PREFIX", str(prefix))
    return project, waiter.resolve()


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.mark.parametrize(
    "missing",
    ["RT_PROJECT_ROOT", "RT_FROM", "RT_SESSION_ID", "RT_LEASE_REVISION"],
)
def test_incomplete_roundtable_environment_is_a_noop(
    tmp_path, monkeypatch, missing
):
    plugin = _load_plugin()
    _set_activation(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    popen = FakePopen([FakeProcess()])
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"](session_id="native-hermes")

    assert not popen.calls
    assert not context.injected


def test_activation_prefers_managed_waiter_and_restarts_after_heartbeat(
    tmp_path, monkeypatch
):
    plugin = _load_plugin()
    project, waiter = _set_activation(monkeypatch, tmp_path, agent="hermes/review")
    blocking = FakeProcess()
    popen = FakePopen(
        [
            ("rt-wait-inbox: heartbeat timeout after 45m\n", 0),
            blocking,
        ]
    )
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"]()
    _wait_until(lambda: len(popen.calls) == 2)

    command, kwargs = popen.calls[0]
    assert command == [str(waiter), "hermes/review"]
    assert kwargs["cwd"] == str(project)
    assert kwargs["env"]["RT_SESSION_ID"] == "session-1"
    assert not context.injected

    context.hooks["on_session_finalize"]()
    assert blocking.terminated


def test_mail_is_injected_once_until_non_ack_mail_is_drained(
    tmp_path, monkeypatch
):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_MAIL_DRAIN_POLL_SECONDS", 0.01)
    project, _waiter = _set_activation(monkeypatch, tmp_path)
    new_dir = project / ".roundtable" / "inbox" / "hermes" / "new"
    new_dir.mkdir(parents=True)
    mail = new_dir / "message-1"
    mail.write_text("body is never read by the adapter", encoding="utf-8")
    (new_dir / "ack-quiet").write_text("", encoding="utf-8")
    blocking = FakeProcess()
    popen = FakePopen(
        [
            ("rt-wait-inbox: mail after 5s:\nmessage-1\n", 0),
            blocking,
        ]
    )
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"]()
    assert context.injected_event.wait(1)
    time.sleep(0.05)

    assert len(context.injected) == 1
    assert len(popen.calls) == 1

    mail.unlink()
    _wait_until(lambda: len(popen.calls) == 2)
    assert len(context.injected) == 1

    context.hooks["on_session_finalize"]()
    assert blocking.terminated


@pytest.mark.parametrize(
    "context",
    [
        FakeContext(inject_result=False),
        FakeContext(inject_error=RuntimeError("CLI unavailable")),
    ],
)
def test_failed_mail_injection_stops_instead_of_waiting_forever(
    tmp_path, monkeypatch, context
):
    plugin = _load_plugin()
    project, _waiter = _set_activation(monkeypatch, tmp_path)
    new_dir = project / ".roundtable" / "inbox" / "hermes" / "new"
    new_dir.mkdir(parents=True)
    (new_dir / "message-1").write_text("pending", encoding="utf-8")
    popen = FakePopen(
        [("rt-wait-inbox: mail after 5s:\nmessage-1\n", 0)]
    )
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)

    plugin.register(context)
    context.hooks["on_session_start"]()
    assert context.injected_event.wait(1)
    _wait_until(lambda: len(context.injected) == 2)

    assert len(popen.calls) == 1
    assert len(context.injected) == 2
    assert "invalid" in context.injected[-1][0]


@pytest.mark.parametrize(
    ("output", "returncode", "expected"),
    [
        (
            "rt-wait-inbox: seat lease or watcher was superseded\n",
            0,
            "superseded",
        ),
        ("rt-wait-inbox: missing claimed-seat environment\n", 2, "invalid"),
    ],
)
def test_fence_or_configuration_failure_stops_with_one_diagnostic(
    tmp_path, monkeypatch, output, returncode, expected
):
    plugin = _load_plugin()
    _set_activation(monkeypatch, tmp_path)
    popen = FakePopen([(output, returncode)])
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"]()
    assert context.injected_event.wait(1)
    time.sleep(0.05)

    assert len(popen.calls) == 1
    assert len(context.injected) == 1
    assert expected in context.injected[0][0]

    context.hooks["on_session_start"]()
    time.sleep(0.05)
    assert len(popen.calls) == 1
    assert len(context.injected) == 1


def test_finalize_and_atexit_cleanup_terminate_the_watcher(
    tmp_path, monkeypatch
):
    plugin = _load_plugin()
    _set_activation(monkeypatch, tmp_path)
    registered = []
    monkeypatch.setattr(plugin.atexit, "register", registered.append)
    first = FakeProcess()
    popen = FakePopen([first])
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"]()
    assert first.communicating.wait(1)
    context.hooks["on_session_finalize"]()
    assert first.terminated

    plugin2 = _load_plugin()
    _set_activation(monkeypatch, tmp_path / "second")
    registered2 = []
    monkeypatch.setattr(plugin2.atexit, "register", registered2.append)
    second = FakeProcess()
    monkeypatch.setattr(plugin2.subprocess, "Popen", FakePopen([second]))
    context2 = FakeContext()
    plugin2.register(context2)
    context2.hooks["on_session_start"]()
    assert second.communicating.wait(1)
    registered2[0]()

    assert second.terminated


def test_finalize_allows_a_new_native_session_in_the_same_cli(
    tmp_path, monkeypatch
):
    plugin = _load_plugin()
    _set_activation(monkeypatch, tmp_path)
    first = FakeProcess()
    second = FakeProcess()
    popen = FakePopen([first, second])
    monkeypatch.setattr(plugin.subprocess, "Popen", popen)
    context = FakeContext()

    plugin.register(context)
    context.hooks["on_session_start"]()
    assert first.communicating.wait(1)
    context.hooks["on_session_finalize"]()
    assert first.terminated

    context.hooks["on_session_start"]()
    assert second.communicating.wait(1)
    context.hooks["on_session_finalize"]()
    assert second.terminated
