from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))

import _rtlauncher


class ExecCalled(Exception):
    pass


def lease(project: Path, agent_id: str, *, revision: int = 1):
    return SimpleNamespace(
        project_root=project.resolve(),
        agent_id=agent_id,
        session_id=f"session-{revision}",
        revision=revision,
    )


def write_project(project: Path, *, agent_id: str, harness: str) -> Path:
    state = project / ".roundtable"
    state.mkdir(parents=True)
    (state / "agents.yaml").write_text(
        "schema: roundtable.agents.v1\n"
        f"project: {project.resolve()}\n"
        "agents:\n"
        f"  {agent_id}:\n"
        f"    harness: {harness}\n"
        "    instances:\n"
        f"      - id: {agent_id}\n"
    )
    return project.resolve()


def clear_lease_environment(monkeypatch) -> None:
    for name in (
        *_rtlauncher.LEASE_ENV_NAMES,
        "RT_RUNTIME_DIR",
        "RT_CODEX_RUNTIME_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_anchored_launcher_claims_seat_and_exports_lease_environment(
    tmp_path, monkeypatch
):
    project = write_project(
        tmp_path / "project", agent_id="claude", harness="claude-code"
    )
    fake_binary = tmp_path / "claude"
    observed = {}
    calls = []

    clear_lease_environment(monkeypatch)
    monkeypatch.setenv("RT_PROJECT_ROOT", "/stale/project")
    monkeypatch.setenv("RT_FROM", "claude")
    monkeypatch.setenv("RT_SESSION_ID", "stale-session")
    monkeypatch.setenv("RT_LEASE_REVISION", "6")
    monkeypatch.setattr(
        _rtlauncher, "choose_launch_cwd", lambda _harness: project
    )
    monkeypatch.setattr(_rtlauncher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(
        _rtlauncher, "harness_bin", lambda _harness: fake_binary
    )

    def fake_claim(root, agent_id, harness):
        calls.append((root, agent_id, harness))
        return lease(project, agent_id, revision=7)

    def fake_execv(program, command):
        observed["program"] = program
        observed["command"] = command
        observed["environment"] = {
            name: os.environ.get(name) for name in _rtlauncher.LEASE_ENV_NAMES
        }
        raise ExecCalled

    monkeypatch.setattr(_rtlauncher, "claim", fake_claim)
    monkeypatch.setattr(_rtlauncher.os, "execv", fake_execv)

    with pytest.raises(ExecCalled):
        _rtlauncher.launch("claude", ["--resume"])

    assert calls == [(project, "claude", "claude")]
    assert observed == {
        "program": str(fake_binary),
        "command": [str(fake_binary), "--resume"],
        "environment": {
            "RT_PROJECT_ROOT": str(project),
            "RT_FROM": "claude",
            "RT_SESSION_ID": "session-7",
            "RT_LEASE_REVISION": "7",
        },
    }


def test_unanchored_launcher_does_not_claim_a_seat(tmp_path, monkeypatch):
    fake_binary = tmp_path / "hermes"
    observed = {}

    clear_lease_environment(monkeypatch)
    monkeypatch.setenv("RT_PROJECT_ROOT", "/inherited/project")
    monkeypatch.setenv("RT_FROM", "manual-identity")
    monkeypatch.setenv("RT_SESSION_ID", "inherited-session")
    monkeypatch.setenv("RT_LEASE_REVISION", "99")
    monkeypatch.setattr(
        _rtlauncher, "choose_launch_cwd", lambda _harness: None
    )
    monkeypatch.setattr(
        _rtlauncher, "project_at_or_above", lambda _cwd: None
    )
    monkeypatch.setattr(
        _rtlauncher, "harness_bin", lambda _harness: fake_binary
    )

    def unexpected_claim(*_args, **_kwargs):
        raise AssertionError("unanchored launch must not claim a seat")

    def fake_execv(program, command):
        observed.update(
            program=program,
            command=command,
            environment={
                name: os.environ.get(name)
                for name in _rtlauncher.LEASE_ENV_NAMES
            },
        )
        raise ExecCalled

    monkeypatch.setattr(_rtlauncher, "claim", unexpected_claim)
    monkeypatch.setattr(_rtlauncher.os, "execv", fake_execv)

    with pytest.raises(ExecCalled):
        _rtlauncher.launch("hermes", ["--continue"])

    assert observed == {
        "program": str(fake_binary),
        "command": [str(fake_binary), "--continue"],
        "environment": {
            "RT_PROJECT_ROOT": None,
            "RT_FROM": "manual-identity",
            "RT_SESSION_ID": None,
            "RT_LEASE_REVISION": None,
        },
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active_healthy", "active"),
        ("active_unhealthy", "unhealthy"),
    ],
)
def test_occupied_seat_is_a_clear_selection_error(
    tmp_path, monkeypatch, status, expected
):
    project = (tmp_path / "project").resolve()

    class Occupied(RuntimeError):
        def __init__(self):
            self.inspection = SimpleNamespace(
                status=status,
                detail=f"seat is {status}",
                token=SimpleNamespace(agent_id="claude-build"),
            )

    def occupied(*_args, **_kwargs):
        raise Occupied

    monkeypatch.setattr(_rtlauncher, "SeatOccupied", Occupied)
    monkeypatch.setattr(_rtlauncher, "claim", occupied)

    with pytest.raises(_rtlauncher.SelectionError, match=expected) as captured:
        _rtlauncher.claim_launch_seat(project, "claude", "claude")
    assert "seat 'claude-build'" in str(captured.value)
    assert "requested seat 'claude'" in str(captured.value)


def test_explicit_identity_must_belong_to_selected_project_and_harness(
    tmp_path, monkeypatch
):
    project = write_project(
        tmp_path / "project", agent_id="claude", harness="claude-code"
    )
    monkeypatch.setenv("RT_FROM", "claude-review")

    with pytest.raises(
        _rtlauncher.SelectionError,
        match="RT_FROM='claude-review' is not configured",
    ):
        _rtlauncher.set_launch_identity(project, "claude")


def test_configured_instance_id_must_be_mailbox_safe(tmp_path):
    project = write_project(
        tmp_path / "project", agent_id="codex", harness="codex"
    )
    (project / ".roundtable" / "agents.yaml").write_text(
        "schema: roundtable.agents.v1\n"
        f"project: {project}\n"
        "agents:\n"
        "  codex:\n"
        "    harness: codex\n"
        "    instances:\n"
        "      - id: ../outside\n"
    )

    with pytest.raises(
        _rtlauncher.SelectionError,
        match="configured instance id",
    ):
        _rtlauncher.configured_sender_ids(project, "codex")


def test_codex_propagates_claimed_seat_to_remote_tool_environment(
    tmp_path, monkeypatch
):
    project = write_project(
        tmp_path / "project", agent_id="codex", harness="codex"
    )
    fake_binary = tmp_path / "codex"
    observed = {}
    launch_order = []
    user_override = 'shell_environment_policy.set={MY_EXISTING_VALUE="keep"}'
    user_argv = ["-c", user_override, "--model", "gpt-5.6"]
    custom_runtime = (tmp_path / "custom-runtime").resolve()

    clear_lease_environment(monkeypatch)
    monkeypatch.setenv("RT_FROM", "codex")
    monkeypatch.setenv("RT_RUNTIME_DIR", str(custom_runtime))
    monkeypatch.delenv("RT_CODEX_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(
        _rtlauncher, "choose_launch_cwd", lambda _harness: project
    )
    monkeypatch.setattr(_rtlauncher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(
        _rtlauncher, "harness_bin", lambda _harness: fake_binary
    )
    monkeypatch.setattr(
        _rtlauncher,
        "preflight_codex_services",
        lambda *, ready_action: (
            launch_order.append("preflight"),
            ready_action(),
        ),
    )

    def claim_after_preflight(root, agent_id, harness):
        launch_order.append("claim")
        return lease(root, agent_id, revision=11)

    monkeypatch.setattr(
        _rtlauncher,
        "claim",
        claim_after_preflight,
    )

    def fake_execv(program, command):
        observed["program"] = program
        observed["command"] = command
        observed["environment"] = {
            name: os.environ.get(name)
            for name in _rtlauncher.CODEX_TOOL_ENV_NAMES
        }
        raise ExecCalled

    monkeypatch.setattr(_rtlauncher.os, "execv", fake_execv)

    with pytest.raises(ExecCalled):
        _rtlauncher.launch("codex", user_argv)

    injected = []
    for name, value in observed["environment"].items():
        injected.extend(
            [
                "-c",
                f"shell_environment_policy.set.{name}={_rtlauncher.json.dumps(value)}",
            ]
        )
    assert observed["program"] == str(fake_binary)
    assert observed["command"] == [
        str(fake_binary),
        "--remote",
        "unix://",
        *user_argv,
        *injected,
    ]
    assert observed["command"].count(user_override) == 1
    assert observed["environment"] == {
        "RT_PROJECT_ROOT": str(project),
        "RT_FROM": "codex",
        "RT_SESSION_ID": "session-11",
        "RT_LEASE_REVISION": "11",
        "RT_RUNTIME_DIR": str(custom_runtime),
        "RT_CODEX_RUNTIME_DIR": str(custom_runtime),
    }
    assert launch_order == ["preflight", "claim"]


def test_unanchored_codex_fails_before_preflight_or_exec(tmp_path, monkeypatch):
    clear_lease_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(_rtlauncher, "choose_launch_cwd", lambda _harness: None)
    monkeypatch.setattr(_rtlauncher, "project_at_or_above", lambda _cwd: None)
    monkeypatch.setattr(
        _rtlauncher,
        "preflight_codex_services",
        lambda **_kwargs: calls.append("preflight"),
    )
    monkeypatch.setattr(
        _rtlauncher.os,
        "execv",
        lambda *_args: calls.append("exec"),
    )

    with pytest.raises(_rtlauncher.SelectionError, match="requires a Roundtable project"):
        _rtlauncher.launch("codex", [])

    assert calls == []


def test_codex_injects_reserved_overrides_before_double_dash(monkeypatch):
    environment = {
        "RT_PROJECT_ROOT": "/project",
        "RT_FROM": "codex",
        "RT_SESSION_ID": "session",
        "RT_LEASE_REVISION": "3",
        "RT_RUNTIME_DIR": "/custom/runtime",
        "RT_CODEX_RUNTIME_DIR": "/custom/runtime",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = _rtlauncher.append_codex_seat_overrides(
        ["--model", "gpt-5.6", "--", "literal prompt"]
    )

    separator = result.index("--")
    assert result[separator + 1 :] == ["literal prompt"]
    for name, value in environment.items():
        assert (
            f"shell_environment_policy.set.{name}="
            f"{_rtlauncher.json.dumps(value)}"
        ) in result[:separator]
