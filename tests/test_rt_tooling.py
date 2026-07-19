import json
import os
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def run_tool(name, *args, cwd=None, env=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(BIN / name), *args],
        cwd=cwd or ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_executable(name, *args, cwd=None, env=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [str(BIN / name), *args],
        cwd=cwd or ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_project(path, *, workspace_title=None, runtime=None):
    state = path / ".roundtable"
    state.mkdir(parents=True)
    title_line = f"workspace_title: {workspace_title}\n" if workspace_title else ""
    (state / "agents.yaml").write_text(
        f"""schema: roundtable.agents.v1
project: {path}
{title_line}agents:
  codex:
    instances:
      - id: codex
        session_id: null
  claude:
    instances:
      - id: claude
        session_id: null
  hermes:
    instances:
      - id: hermes
        session_id: null
"""
    )
    (state / "messages").mkdir()
    (state / "locks").mkdir()
    if runtime is not None:
        (state / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    return state


def runtime_for(workspace="workspace:7", surface="surface:8", pane="pane:9"):
    route = {
        "workspace_ref": workspace,
        "surface_ref": surface,
        "pane_ref": pane,
        "status": "idle",
    }
    return {
        "schema": "roundtable.runtime.v1",
        "project": "",
        "updated_at": "2026-06-10T00:00:00Z",
        "workspace_ref": workspace,
        "workspace_title": "Bound Workspace",
        "window_ref": "window:1",
        "caller": {},
        "agents": {"codex": route},
        "surfaces": [route],
    }


def fake_cmux(tmp_path, *, tree, identify=None, screens=None):
    fake = tmp_path / "fake-bin" / "cmux"
    fake.parent.mkdir()
    fake.write_text(
        f"""#!{sys.executable}
import json
import os
import sys

args = sys.argv[1:]
tree = json.loads(os.environ["CMUX_FAKE_TREE"])
identify = json.loads(os.environ.get("CMUX_FAKE_IDENTIFY", "{{}}"))
screens = json.loads(os.environ.get("CMUX_FAKE_SCREENS", "{{}}"))
if args[:1] == ["tree"]:
    print(json.dumps(tree))
elif args[:1] == ["identify"]:
    print(json.dumps(identify))
elif args[:1] == ["read-screen"]:
    surface = ""
    for idx, arg in enumerate(args):
        if arg == "--surface" and idx + 1 < len(args):
            surface = args[idx + 1]
            break
    print(screens.get(surface, ""))
elif args[:1] == ["events"]:
    sys.exit(0)
elif args[:1] in (["send"], ["send-key"]):
    sys.exit(0)
else:
    print("unexpected cmux args: " + " ".join(args), file=sys.stderr)
    sys.exit(64)
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{fake.parent}:{os.environ.get('PATH', '')}",
        "CMUX_FAKE_TREE": json.dumps(tree),
        "CMUX_FAKE_IDENTIFY": json.dumps(identify or {}),
        "CMUX_FAKE_SCREENS": json.dumps(screens or {}),
    }
    return env


def tree_with_workspaces(*workspaces):
    return {
        "caller": None,
        "windows": [
            {
                "ref": "window:1",
                "workspaces": list(workspaces),
            }
        ],
    }


def workspace(ref, title, surface_ref="surface:10", pane_ref="pane:10", surface_title="Codex"):
    return {
        "ref": ref,
        "title": title,
        "panes": [
            {
                "ref": pane_ref,
                "surfaces": [
                    {
                        "ref": surface_ref,
                        "pane_ref": pane_ref,
                        "type": "terminal",
                        "title": surface_title,
                        "selected": True,
                        "focused": True,
                        "here": False,
                    }
                ],
            }
        ],
    }


def test_rt_resolve_uses_fallback_project_when_cwd_is_not_project(tmp_path):
    project = tmp_path / "commons"
    runtime = runtime_for()
    runtime["project"] = str(project)
    write_project(project, runtime=runtime)
    outside = tmp_path / "outside"
    outside.mkdir()

    proc = run_tool(
        "rt-resolve",
        "codex",
        cwd=outside,
        env={"RT_FALLBACK_PROJECT": str(project)},
    )

    assert proc.returncode == 0, proc.stderr
    assert "workspace=workspace:7" in proc.stdout
    assert "surface=surface:8" in proc.stdout


def test_rt_say_refuses_sync_ack_outside_ack_mode_before_refresh(tmp_path):
    project = tmp_path / "project"
    write_project(project)

    proc = run_tool(
        "rt-say",
        "codex",
        "sync-ack",
        "refs=20260610T000000Z-claude-to-codex-12345",
        cwd=project,
        env={"RT_FROM": "claude"},
    )

    assert proc.returncode != 0
    assert "rt-ack" in proc.stderr


def test_rt_ack_still_sends_sync_ack_in_ack_mode(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    active = workspace("workspace:1", "project", "surface:1", "pane:1", "Codex")
    active["panes"].append(
        {
            "ref": "pane:2",
            "surfaces": [
                {
                    "ref": "surface:2",
                    "pane_ref": "pane:2",
                    "type": "terminal",
                    "title": "Claude",
                    "selected": True,
                    "focused": False,
                    "here": False,
                }
            ],
        }
    )
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(active),
        identify={"caller": {"workspace_ref": "workspace:1", "surface_ref": "surface:1"}},
    )
    env["RT_FROM"] = "codex"

    proc = run_tool(
        "rt-ack",
        "20260610T000000Z-claude-to-codex-12345",
        "received",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "sent 2026" in proc.stdout
    ledger = (state / "messages" / "codex.jsonl").read_text()
    assert '"kind":"sync-ack"' in ledger
    assert "20260610T000000Z-claude-to-codex-12345" in ledger


def test_rt_inbox_lists_unacked_messages_for_inferred_agent_and_hides_acked(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    ledger = state / "messages" / "claude.jsonl"
    records = [
        {
            "schema": "roundtable.message_event.v1",
            "msg_id": "20260610T000000Z-claude-to-codex-11111",
            "event_id": "1",
            "ts": "2026-06-10T00:00:00.000Z",
            "from": "claude",
            "to": "codex",
            "kind": "question",
            "body": "hello codex",
            "lifecycle": "submitted",
        },
        {
            "schema": "roundtable.message_event.v1",
            "msg_id": "20260610T000001Z-claude-to-codex-22222",
            "event_id": "2",
            "ts": "2026-06-10T00:00:01.000Z",
            "from": "claude",
            "to": "codex",
            "kind": "fyi",
            "body": "already acked",
            "lifecycle": "acked",
        },
        {
            "schema": "roundtable.message_event.v1",
            "msg_id": "20260610T000002Z-claude-to-codex-33333",
            "event_id": "3",
            "ts": "2026-06-10T00:00:02.000Z",
            "from": "claude",
            "to": "codex",
            "kind": "sync-ack",
            "body": "refs=xxx",
            "lifecycle": "submitted",
        },
    ]
    ledger.write_text("".join(json.dumps(item) + "\n" for item in records))

    proc = run_tool("rt-inbox", cwd=project, env={"RT_FROM": "codex"})

    assert proc.returncode == 0, proc.stderr
    assert "20260610T000000Z-claude-to-codex-11111" in proc.stdout
    assert "already acked" not in proc.stdout
    assert "sync-ack" not in proc.stdout


def test_rt_inbox_json_all_outputs_current_records(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    (state / "messages" / "claude.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "msg_id": "20260610T000000Z-claude-to-codex-11111",
                        "ts": "2026-06-10T00:00:00.000Z",
                        "from": "claude",
                        "to": "codex",
                        "kind": "question",
                        "body": "old",
                        "lifecycle": "submitted",
                    }
                ),
                json.dumps(
                    {
                        "msg_id": "20260610T000000Z-claude-to-codex-11111",
                        "ts": "2026-06-10T00:00:01.000Z",
                        "from": "claude",
                        "to": "codex",
                        "kind": "question",
                        "body": "new",
                        "lifecycle": "accepted",
                    }
                ),
            ]
        )
        + "\n"
    )

    proc = run_tool("rt-inbox", "--all", "-f", "json", cwd=project, env={"RT_FROM": "codex"})

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert len(payload) == 1
    assert payload[0]["lifecycle"] == "accepted"
    assert payload[0]["body"] == "new"


def test_rt_say_inbox_ack_flow_with_fake_cmux(tmp_path):
    project = tmp_path / "project"
    write_project(project)
    active = workspace("workspace:1", "project", "surface:1", "pane:1", "Codex")
    active["panes"].append(
        {
            "ref": "pane:2",
            "surfaces": [
                {
                    "ref": "surface:2",
                    "pane_ref": "pane:2",
                    "type": "terminal",
                    "title": "Claude",
                    "selected": True,
                    "focused": False,
                    "here": False,
                }
            ],
        }
    )
    base_env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(active),
        identify={"caller": {"workspace_ref": "workspace:1", "surface_ref": "surface:1"}},
    )

    send_proc = run_tool("rt-say", "claude", "question", "please review", cwd=project, env=base_env)

    assert send_proc.returncode == 0, send_proc.stderr
    msg_id = send_proc.stdout.strip().split()[-1]
    inbox_proc = run_tool("rt-inbox", cwd=project, env={**base_env, "RT_FROM": "claude"})
    assert msg_id in inbox_proc.stdout
    assert "please review" in inbox_proc.stdout

    ack_env = {
        **base_env,
        "RT_FROM": "claude",
        "CMUX_FAKE_IDENTIFY": json.dumps({"caller": {"workspace_ref": "workspace:1", "surface_ref": "surface:2"}}),
    }
    ack_proc = run_tool("rt-ack", msg_id, "received", cwd=project, env=ack_env)

    assert ack_proc.returncode == 0, ack_proc.stderr
    after_ack = run_tool("rt-inbox", cwd=project, env={**base_env, "RT_FROM": "claude"})
    assert msg_id not in after_ack.stdout


def test_rt_refresh_bind_persists_explicit_workspace(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    bound = workspace("workspace:9", "Unrelated Workspace", "surface:9", "pane:9", "Codex bound")
    other = workspace("workspace:2", "Other", "surface:2", "pane:2", "Other")
    env = fake_cmux(tmp_path, tree=tree_with_workspaces(other, bound))

    proc = run_tool("rt-refresh", "--bind", "workspace:9", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_ref"] == "workspace:9"
    assert runtime["workspace_binding"]["ref"] == "workspace:9"
    assert runtime["workspace_binding"]["title"] == "Unrelated Workspace"


def workspace_with_review_surfaces():
    return {
        "ref": "workspace:4",
        "title": "Unrelated Workspace",
        "panes": [
            {
                "ref": "pane:14",
                "surfaces": [
                    {
                        "ref": "surface:23",
                        "pane_ref": "pane:14",
                        "type": "terminal",
                        "title": "Check computer security, optimize files and home network",
                        "selected": True,
                        "focused": True,
                        "here": False,
                    }
                ],
            },
            {
                "ref": "pane:15",
                "surfaces": [
                    {
                        "ref": "surface:25",
                        "pane_ref": "pane:15",
                        "type": "terminal",
                        "title": "hermes ~",
                        "selected": True,
                        "focused": False,
                        "here": False,
                    }
                ],
            },
            {
                "ref": "pane:16",
                "surfaces": [
                    {
                        "ref": "surface:24",
                        "pane_ref": "pane:16",
                        "type": "terminal",
                        "title": "developer",
                        "selected": True,
                        "focused": False,
                        "here": False,
                    }
                ],
            },
        ],
    }


def test_rt_refresh_never_assigns_focused_surface_to_codex_without_caller(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    workspace_data = workspace_with_review_surfaces()
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(workspace_data),
        identify={"caller": None, "focused": {"workspace_ref": "workspace:4", "surface_ref": "surface:23"}},
        screens={
            "surface:23": "Claude prompt",
            "surface:24": "gpt-5.5 xhigh · ~ · Context 39% left",
            "surface:25": "Welcome to Hermes Agent",
        },
    )

    proc = run_tool("rt-refresh", "--bind", "workspace:4", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["agents"]["codex"]["surface_ref"] == "surface:24"
    assert runtime["agents"]["codex"]["pane_ref"] == "pane:16"
    assert runtime["agents"]["claude"]["surface_ref"] == "surface:23"
    assert runtime["agents"]["claude"]["pane_ref"] == "pane:14"


def test_rt_refresh_bind_current_uses_focused_for_binding_not_agent_assignment(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    workspace_data = workspace_with_review_surfaces()
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(workspace_data),
        identify={"caller": None, "focused": {"workspace_ref": "workspace:4", "surface_ref": "surface:23"}},
        screens={
            "surface:23": "Claude prompt",
            "surface:24": "OpenAI Codex (v0.0.0)",
            "surface:25": "Welcome to Hermes Agent",
        },
    )

    proc = run_tool("rt-refresh", "--bind-current", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_ref"] == "workspace:4"
    assert runtime["workspace_binding"]["ref"] == "workspace:4"
    assert runtime["agents"]["codex"]["surface_ref"] == "surface:24"
    assert runtime["agents"]["claude"]["surface_ref"] == "surface:23"


def test_rt_refresh_uses_agents_yaml_workspace_title_as_exact_match(tmp_path):
    project = tmp_path / "project"
    state = write_project(project, workspace_title="Configured Workspace")
    configured = workspace("workspace:5", "Configured Workspace", "surface:5", "pane:5", "Codex")
    other = workspace("workspace:6", "project", "surface:6", "pane:6", "Other")
    env = fake_cmux(tmp_path, tree=tree_with_workspaces(other, configured))

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_ref"] == "workspace:5"


def test_roundtable_init_next_steps_include_binding_and_watcher(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()

    proc = run_tool("roundtable-init", "--no-git", "-p", str(parent), "sample")

    assert proc.returncode == 0, proc.stderr
    assert "rt-refresh --bind-current" in proc.stdout
    assert "rt-watch-ensure" in proc.stdout


def test_rt_watch_ensure_resolves_rt_watch_bin_from_install_dir(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    env = fake_cmux(tmp_path, tree=tree_with_workspaces(), identify={})

    proc = run_executable("rt-watch-ensure", cwd=project, env=env)

    assert proc.returncode == 0
    assert "SCRIPT_DIR" not in proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "cannot start watcher surface" in (state / "rt-watch.log").read_text()
