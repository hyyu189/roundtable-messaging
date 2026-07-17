import json
import os
import stat
import subprocess
import sys
import time
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
    harness: codex
    submit:
      idle: enter
      busy: tab
    instances:
      - id: codex
        session_id: null
    detect:
      screen: ["OpenAI Codex"]
  claude:
    harness: claude-code
    submit:
      idle: enter
      busy: send_only
    instances:
      - id: claude
        session_id: null
    detect:
      screen: ["Claude Code"]
  hermes:
    harness: hermes-agent
    submit:
      idle: enter
      busy: steer
    instances:
      - id: hermes
        session_id: null
    detect:
      screen: ["Welcome to Hermes Agent"]
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


def fake_cmux(
    tmp_path,
    *,
    tree,
    identify=None,
    screens=None,
    surface_list=None,
    surface_workspace=None,
):
    all_workspaces = [
        workspace_data
        for window in tree.get("windows", [])
        for workspace_data in window.get("workspaces", [])
    ]
    if surface_workspace is None:
        context = (
            (identify or {}).get("caller")
            or (identify or {}).get("focused")
            or (identify or {}).get("active")
            or {}
        )
        context_ref = context.get("workspace_ref")
        surface_workspace = next(
            (item for item in all_workspaces if item.get("ref") == context_ref),
            all_workspaces[0] if len(all_workspaces) == 1 else None,
        )

    if surface_list is None:
        surface_list = []
        for pane in (surface_workspace or {}).get("panes", []):
            for surface in pane.get("surfaces", []):
                item = dict(surface)
                title = (surface.get("title") or "").lower()
                kind = None
                if "codex" in title:
                    kind = "codex"
                elif "claude" in title:
                    kind = "claude"
                elif "hermes" in title:
                    kind = "hermes-agent"
                if kind:
                    item["resume_binding"] = {
                        "kind": kind,
                        "checkpoint_id": f"checkpoint-{surface.get('ref')}",
                        "updated_at": 1,
                    }
                surface_list.append(item)

    surface_payload = {
        "surfaces": surface_list,
        "workspace_ref": (surface_workspace or {}).get("ref"),
        "workspace_id": (surface_workspace or {}).get("id"),
    }

    fake = tmp_path / "fake-bin" / "cmux"
    fake.parent.mkdir()
    fake.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]
tree = json.loads(os.environ["CMUX_FAKE_TREE"])
identify = json.loads(os.environ.get("CMUX_FAKE_IDENTIFY", "{{}}"))
screens = json.loads(os.environ.get("CMUX_FAKE_SCREENS", "{{}}"))
surface_payload = json.loads(os.environ.get("CMUX_FAKE_SURFACE_LIST", "{{}}"))
trace_dir = os.environ.get("CMUX_FAKE_TRACE_DIR")
if trace_dir:
    trace_path = Path(trace_dir)
    trace_path.mkdir(parents=True, exist_ok=True)
    (trace_path / f"{{time.time_ns()}}-{{os.getpid()}}-{{uuid.uuid4().hex}}.json").write_text(json.dumps(args))
if args[:1] == ["tree"]:
    print(json.dumps(tree))
elif args[:1] == ["identify"]:
    print(json.dumps(identify))
elif args[:2] == ["rpc", "surface.list"]:
    print(json.dumps(surface_payload))
elif args[:1] == ["read-screen"]:
    surface = ""
    for idx, arg in enumerate(args):
        if arg == "--surface" and idx + 1 < len(args):
            surface = args[idx + 1]
            break
    print(screens.get(surface, ""))
elif args[:1] == ["events"]:
    sys.exit(0)
elif args[:1] == ["send"]:
    delay = float(os.environ.get("CMUX_FAKE_SEND_DELAY", "0"))
    if delay:
        time.sleep(delay)
    if os.environ.get("CMUX_FAKE_FAIL_SEND") == "1":
        sys.exit(70)
    sys.exit(0)
elif args[:1] == ["send-key"]:
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
        "CMUX_FAKE_SURFACE_LIST": json.dumps(surface_payload),
    }
    return env


def read_cmux_calls(trace_dir):
    return [json.loads(path.read_text()) for path in sorted(trace_dir.glob("*.json"))]


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


def workspace(
    ref,
    title,
    surface_ref="surface:10",
    pane_ref="pane:10",
    surface_title="Codex",
    workspace_id=None,
):
    return {
        "id": workspace_id or f"uuid-{ref}",
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


def bound_runtime(
    project,
    workspace_ref,
    workspace_id=None,
    *,
    title="Bound Workspace",
    surface_ref="surface:8",
    pane_ref="pane:9",
):
    runtime = runtime_for(workspace_ref, surface_ref, pane_ref)
    runtime["project"] = str(project)
    runtime["workspace_title"] = title
    binding = {
        "ref": workspace_ref,
        "title": title,
        "source": "existing",
        "updated_at": "2026-06-10T00:00:00Z",
    }
    if workspace_id:
        runtime["workspace_id"] = workspace_id
        binding["workspace_id"] = workspace_id
    runtime["workspace_binding"] = binding
    return runtime


def say_project(tmp_path, *, target_status="idle"):
    project = tmp_path / "project"
    codex_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:1",
        "pane_ref": "pane:1",
        "status": "idle",
    }
    claude_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:2",
        "pane_ref": "pane:2",
        "status": target_status,
    }
    runtime = {
        "schema": "roundtable.runtime.v1",
        "project": str(project),
        "updated_at": "2026-06-10T00:00:00Z",
        "workspace_ref": "workspace:1",
        "workspace_id": "UUID-A",
        "workspace_title": "project",
        "window_ref": "window:1",
        "caller": {},
        "agents": {"codex": codex_route, "claude": claude_route},
        "surfaces": [codex_route, claude_route],
    }
    state = write_project(project, runtime=runtime)
    active = workspace(
        "workspace:1",
        "project",
        "surface:1",
        "pane:1",
        "Codex",
        workspace_id="UUID-A",
    )
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
        identify={
            "caller": {
                "workspace_ref": "workspace:1",
                "workspace_id": "UUID-A",
                "surface_ref": "surface:1",
            }
        },
    )
    trace_dir = tmp_path / "cmux-trace"
    trace_dir.mkdir()
    env["CMUX_FAKE_TRACE_DIR"] = str(trace_dir)
    return project, state, env, trace_dir


def read_ledger(state, sender="codex"):
    path = state / "messages" / f"{sender}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_mail(state, target, msg_id, sender, kind, body, folder="new"):
    directory = state / "inbox" / target / folder
    directory.mkdir(parents=True, exist_ok=True)
    content = f"[{sender.upper()}→{target.upper()} {kind} id={msg_id}]"
    if body:
        content += f" {body}"
    (directory / f"{msg_id}.md").write_text(content)


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


def test_project_discovery_does_not_fallback_to_ref_when_runtime_uuid_differs(tmp_path):
    first = tmp_path / "a-project"
    first_runtime = bound_runtime(
        first,
        "workspace:1",
        surface_ref="surface:11",
        pane_ref="pane:11",
    )
    write_project(first, runtime=first_runtime)

    second = tmp_path / "b-project"
    second_runtime = bound_runtime(
        second,
        "workspace:1",
        "UUID-B",
        surface_ref="surface:22",
        pane_ref="pane:22",
    )
    write_project(second, runtime=second_runtime)

    outside = tmp_path / "outside"
    outside.mkdir()
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(),
        identify={"caller": {"workspace_ref": "workspace:1", "workspace_id": "UUID-B"}},
    )
    env["RT_PROJECTS_DIR"] = str(tmp_path)

    proc = run_tool("rt-resolve", "codex", cwd=outside, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "surface=surface:22" in proc.stdout
    assert "surface=surface:11" not in proc.stdout


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
    write_mail(
        state,
        "codex",
        "20260610T000001Z-claude-to-codex-22222",
        "claude",
        "fyi",
        "already acked mail copy",
    )

    proc = run_tool("rt-inbox", cwd=project, env={"RT_FROM": "codex"})

    assert proc.returncode == 0, proc.stderr
    assert "20260610T000000Z-claude-to-codex-11111" in proc.stdout
    assert "20260610T000001Z-claude-to-codex-22222" not in proc.stdout
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


def test_rt_say_default_dual_writes_exact_mail_and_preserves_legacy_nudge(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    body = "line 1\nline 2  with  spaces  "

    proc = run_tool("rt-say", "claude", "question", body, cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    msg_id = proc.stdout.strip().split()[-1]
    new_file = state / "inbox" / "claude" / "new" / f"{msg_id}.md"
    assert new_file.read_text() == f"[CODEX→CLAUDE question id={msg_id}] {body}"
    assert list((state / "inbox" / "claude" / "tmp").iterdir()) == []
    assert list((state / "inbox" / "claude" / "cur").iterdir()) == []

    records = read_ledger(state)
    assert [record["lifecycle"] for record in records] == ["pending", "injected", "submitted"]
    assert all(record["msg_id"] == msg_id for record in records)
    assert all(record["body"] == body for record in records)
    calls = read_cmux_calls(trace_dir)
    send_calls = [call for call in calls if call[:1] == ["send"]]
    key_calls = [call for call in calls if call[:1] == ["send-key"]]
    assert send_calls == [
        [
            "send",
            "--workspace",
            "workspace:1",
            "--surface",
            "surface:2",
            records[0]["send_text"],
        ]
    ]
    assert records[0]["send_text"] == f"[CODEX→CLAUDE question id={msg_id}] line 1 line 2 with spaces"
    assert key_calls == [
        ["send-key", "--workspace", "workspace:1", "--surface", "surface:2", "Enter"]
    ]


def test_rt_say_default_preserves_busy_submit_policy_per_harness(tmp_path):
    cases = [
        ("claude", "codex", "surface:2", "none", None, False),
        ("codex", "claude", "surface:1", "Tab", "Tab", False),
        ("hermes", "codex", "surface:3", "Enter", "Enter", True),
    ]
    for index, (target, sender, surface, submit, key, steer) in enumerate(cases):
        case_dir = tmp_path / f"case-{index}"
        project, state, env, trace_dir = say_project(
            case_dir,
            target_status="busy" if target == "claude" else "idle",
        )
        runtime_path = state / "runtime.json"
        runtime = json.loads(runtime_path.read_text())
        if target == "codex":
            runtime["agents"]["codex"]["status"] = "busy"
            env["CMUX_FAKE_IDENTIFY"] = json.dumps(
                {"caller": {"workspace_ref": "workspace:1", "surface_ref": "surface:2"}}
            )
        elif target == "hermes":
            route = {
                "workspace_ref": "workspace:1",
                "surface_ref": "surface:3",
                "pane_ref": "pane:3",
                "status": "busy",
            }
            runtime["agents"]["hermes"] = route
            runtime["surfaces"].append(route)
        runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")

        proc = run_tool("rt-say", target, "fyi", "busy delivery", cwd=project, env=env)

        assert proc.returncode == 0, proc.stderr
        record = read_ledger(state, sender)[0]
        assert record["submit"] == submit
        assert record["send_text"].startswith("/steer ") is steer
        calls = read_cmux_calls(trace_dir)
        send_calls = [call for call in calls if call[:1] == ["send"]]
        assert len(send_calls) == 1
        assert send_calls[0][4] == surface
        key_calls = [call for call in calls if call[:1] == ["send-key"]]
        if key is None:
            assert key_calls == []
        else:
            assert key_calls == [
                ["send-key", "--workspace", "workspace:1", "--surface", surface, key]
            ]


def test_rt_say_concurrent_same_target_delivers_both_once(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    env["RT_FROM"] = "codex"
    process_env = os.environ.copy()
    process_env.update(env)

    first = subprocess.Popen(
        [
            sys.executable,
            str(BIN / "rt-say"),
            "--no-nudge",
            "claude",
            "question",
            "first",
        ],
        cwd=project,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = subprocess.Popen(
        [
            sys.executable,
            str(BIN / "rt-say"),
            "--no-nudge",
            "claude",
            "question",
            "second",
        ],
        cwd=project,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    msg_ids = {first_stdout.strip().split()[-1], second_stdout.strip().split()[-1]}
    assert len(msg_ids) == 2
    files = list((state / "inbox" / "claude" / "new").glob("*.md"))
    assert {path.stem for path in files} == msg_ids
    bodies_by_id = {path.stem: path.read_text().rsplit("] ", 1)[-1] for path in files}
    assert bodies_by_id[first_stdout.strip().split()[-1]] == "first"
    assert bodies_by_id[second_stdout.strip().split()[-1]] == "second"
    assert list((state / "inbox" / "claude" / "tmp").iterdir()) == []

    by_id = {}
    for record in read_ledger(state):
        by_id.setdefault(record["msg_id"], []).append(record["lifecycle"])
    assert set(by_id) == msg_ids
    assert all(lifecycles == ["pending"] for lifecycles in by_id.values())
    assert read_cmux_calls(trace_dir) == []


def test_rt_say_contended_legacy_lock_still_fails_fast_after_publishing_mail(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    env["CMUX_FAKE_SEND_DELAY"] = "0.30"
    env["RT_FROM"] = "codex"
    process_env = os.environ.copy()
    process_env.update(env)

    first = subprocess.Popen(
        [sys.executable, str(BIN / "rt-say"), "claude", "question", "first legacy"],
        cwd=project,
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    send_lock = state / "locks" / "send-claude.lock"
    deadline = time.time() + 3
    while not send_lock.exists() and first.poll() is None and time.time() < deadline:
        time.sleep(0.01)
    assert send_lock.exists(), "first rt-say never acquired the target send lock"

    second = run_tool("rt-say", "claude", "question", "second legacy", cwd=project, env=env)
    first_stdout, first_stderr = first.communicate(timeout=10)

    assert first.returncode == 0, first_stderr
    assert second.returncode != 0
    assert "lock busy" in second.stderr
    files = list((state / "inbox" / "claude" / "new").glob("*.md"))
    assert len(files) == 2
    assert {path.read_text().rsplit("] ", 1)[-1] for path in files} == {
        "first legacy",
        "second legacy",
    }
    first_id = first_stdout.strip().split()[-1]
    assert {record["msg_id"] for record in read_ledger(state)} == {first_id}
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1


def test_rt_say_no_nudge_uses_only_maildir_and_pending_ledger(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    env["RT_FROM"] = "codex"

    proc = run_tool(
        "rt-say",
        "--no-nudge",
        "claude",
        "question",
        "mail only",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    msg_id = proc.stdout.strip().split()[-1]
    assert (state / "inbox" / "claude" / "new" / f"{msg_id}.md").is_file()
    assert read_cmux_calls(trace_dir) == []
    records = read_ledger(state)
    assert len(records) == 1
    assert records[0]["lifecycle"] == "pending"
    assert records[0]["submit"] == "none"
    assert records[0]["workspace_ref"] is None
    assert records[0]["surface_ref"] is None


def test_rt_say_no_nudge_rejects_same_multi_instance_but_allows_sibling(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    agents = agents_path.read_text()
    old_instances = """    instances:
      - id: codex
        session_id: null
"""
    new_instances = """    instances:
      - id: codex-build
        session_id: null
      - id: codex-review
        session_id: null
"""
    assert old_instances in agents
    agents_path.write_text(agents.replace(old_instances, new_instances, 1))
    env["RT_FROM"] = "codex-build"

    self_proc = run_tool(
        "rt-say",
        "--no-nudge",
        "codex-build",
        "fyi",
        "self loop",
        cwd=project,
        env=env,
    )

    assert self_proc.returncode != 0
    assert "refusing self-send" in self_proc.stderr
    assert not (state / "inbox").exists()
    assert read_ledger(state, "codex-build") == []

    sibling_proc = run_tool(
        "rt-say",
        "--no-nudge",
        "codex-review",
        "fyi",
        "sibling delivery",
        cwd=project,
        env=env,
    )
    assert sibling_proc.returncode == 0, sibling_proc.stderr
    assert len(read_ledger(state, "codex-build")) == 1
    assert read_cmux_calls(trace_dir) == []


def test_rt_say_legacy_modes_keep_caller_first_sender_inference(tmp_path):
    for index, flag in enumerate((None, "--legacy-nudge-only")):
        project, state, env, _trace_dir = say_project(tmp_path / f"legacy-{index}")
        env["RT_FROM"] = "claude"
        args = ([flag] if flag else []) + ["claude", "fyi", "caller wins"]

        proc = run_tool("rt-say", *args, cwd=project, env=env)

        assert proc.returncode == 0, proc.stderr
        assert len(read_ledger(state, "codex")) == 3
        assert read_ledger(state, "claude") == []

    project, state, env, trace_dir = say_project(tmp_path / "mail-only")
    env["RT_FROM"] = "claude"
    mail_only = run_tool(
        "rt-say",
        "--no-nudge",
        "hermes",
        "fyi",
        "explicit sender",
        cwd=project,
        env=env,
    )
    assert mail_only.returncode == 0, mail_only.stderr
    assert len(read_ledger(state, "claude")) == 1
    assert read_cmux_calls(trace_dir) == []


def test_rt_say_legacy_accepts_runtime_surplus_instances(tmp_path):
    project, state, env, _trace_dir = say_project(tmp_path)
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    codex_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:3",
        "pane_ref": "pane:3",
        "status": "idle",
    }
    claude_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:4",
        "pane_ref": "pane:4",
        "status": "idle",
    }
    runtime["agents"]["codex#1"] = codex_route
    runtime["agents"]["claude#1"] = claude_route
    runtime["surfaces"].extend([codex_route, claude_route])
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env["CMUX_FAKE_IDENTIFY"] = json.dumps(
        {"caller": {"workspace_ref": "workspace:1", "surface_ref": "surface:3"}}
    )

    proc = run_tool("rt-say", "claude#1", "fyi", "surplus instance", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    msg_id = proc.stdout.strip().split()[-1]
    assert (state / "inbox" / "claude#1" / "new" / f"{msg_id}.md").is_file()
    assert len(read_ledger(state, "codex#1")) == 3

    self_proc = run_tool("rt-say", "codex#1", "fyi", "must not loop", cwd=project, env=env)
    assert self_proc.returncode != 0
    assert "refusing self-send" in self_proc.stderr
    assert not (state / "inbox" / "codex#1").exists()
    assert len(read_ledger(state, "codex#1")) == 3


def test_rt_say_rejects_ambiguous_or_unknown_target_before_mail(tmp_path):
    ambiguous_project, ambiguous_state, ambiguous_env, ambiguous_trace = say_project(
        tmp_path / "ambiguous"
    )
    agents_path = ambiguous_state / "agents.yaml"
    agents = agents_path.read_text()
    old_instances = """    instances:
      - id: claude
        session_id: null
"""
    new_instances = """    instances:
      - id: claude-build
        session_id: null
      - id: claude-review
        session_id: null
"""
    assert old_instances in agents
    agents_path.write_text(agents.replace(old_instances, new_instances, 1))

    ambiguous = run_tool(
        "rt-say",
        "claude",
        "fyi",
        "ambiguous target",
        cwd=ambiguous_project,
        env=ambiguous_env,
    )
    assert ambiguous.returncode != 0
    assert "has multiple instances" in ambiguous.stderr
    assert not (ambiguous_state / "inbox").exists()
    assert read_ledger(ambiguous_state) == []
    ambiguous_calls = read_cmux_calls(ambiguous_trace)
    assert [
        call
        for call in ambiguous_calls
        if call[:1] in (["events"], ["send"], ["send-key"])
    ] == []

    unknown_project, unknown_state, unknown_env, unknown_trace = say_project(tmp_path / "unknown")
    unknown = run_tool(
        "rt-say",
        "ghost",
        "fyi",
        "unknown target",
        cwd=unknown_project,
        env=unknown_env,
    )
    assert unknown.returncode != 0
    assert "unknown agent or instance" in unknown.stderr
    assert not (unknown_state / "inbox").exists()
    assert read_ledger(unknown_state) == []
    unknown_calls = read_cmux_calls(unknown_trace)
    assert [call for call in unknown_calls if call[:1] in (["events"], ["send"], ["send-key"])] == []


def test_rt_say_legacy_nudge_only_skips_maildir(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)

    proc = run_tool(
        "rt-say",
        "--legacy-nudge-only",
        "claude",
        "question",
        "legacy only",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (state / "inbox").exists()
    assert [record["lifecycle"] for record in read_ledger(state)] == [
        "pending",
        "injected",
        "submitted",
    ]
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1


def test_rt_say_rejects_conflicting_delivery_flags_without_side_effects(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)

    proc = run_tool(
        "rt-say",
        "--no-nudge",
        "--legacy-nudge-only",
        "claude",
        "question",
        "conflict",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 2
    assert "mutually exclusive" in proc.stderr
    assert not (state / "inbox").exists()
    assert read_ledger(state) == []
    assert read_cmux_calls(trace_dir) == []


def test_rt_say_nudge_failure_leaves_published_mail_and_pending_ledger(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    env["CMUX_FAKE_FAIL_SEND"] = "1"

    proc = run_tool("rt-say", "claude", "question", "survive failure", cwd=project, env=env)

    assert proc.returncode != 0
    files = list((state / "inbox" / "claude" / "new").glob("*.md"))
    assert len(files) == 1
    records = read_ledger(state)
    assert [record["lifecycle"] for record in records] == ["pending"]
    assert files[0].stem == records[0]["msg_id"]
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert [call for call in calls if call[:1] == ["send-key"]] == []


def test_rt_say_route_failure_still_leaves_published_mail(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    runtime["agents"]["claude"]["surface_ref"] = ""
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env["RT_FROM"] = "codex"

    proc = run_tool("rt-say", "claude", "question", "route is broken", cwd=project, env=env)

    assert proc.returncode != 0
    files = list((state / "inbox" / "claude" / "new").glob("*.md"))
    assert len(files) == 1
    assert files[0].read_text().endswith("] route is broken")
    assert read_ledger(state) == []
    calls = read_cmux_calls(trace_dir)
    assert [call for call in calls if call[:1] in (["events"], ["send"], ["send-key"])] == []


def test_rt_say_rejects_invalid_sender_before_mail_or_keyboard_side_effects(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    env["RT_FROM"] = "../codex"

    proc = run_tool(
        "rt-say",
        "--no-nudge",
        "claude",
        "question",
        "unsafe sender",
        cwd=project,
        env=env,
    )

    assert proc.returncode != 0
    assert "invalid sender agent component" in proc.stderr
    assert not (state / "inbox").exists()
    assert read_ledger(state) == []
    assert read_cmux_calls(trace_dir) == []


def test_rt_say_mail_failure_prevents_ledger_and_keyboard_side_effects(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    (state / "inbox").write_text("not a directory")

    proc = run_tool("rt-say", "claude", "question", "must not nudge", cwd=project, env=env)

    assert proc.returncode != 0
    assert "failed to publish inbox message" in proc.stderr
    assert read_ledger(state) == []
    calls = read_cmux_calls(trace_dir)
    assert [call for call in calls if call[:1] in (["events"], ["send"], ["send-key"])] == []


def test_rt_say_help_documents_dual_delivery_and_dedup(tmp_path):
    project = tmp_path / "project"
    write_project(project)

    proc = run_tool("rt-say", "--help", cwd=project)

    assert proc.returncode == 0, proc.stderr
    assert "--no-nudge" in proc.stdout
    assert "--legacy-nudge-only" in proc.stdout
    assert "dual-write" in proc.stdout
    assert "deduplicate using the msgid" in proc.stdout


def test_rt_inbox_shows_ledger_and_maildir_copies_with_source_labels(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    both_id = "20260716T010000Z-codex-to-claude-11111"
    ledger_id = "20260716T010001Z-codex-to-claude-22222"
    mail_id = "20260716T010002Z-codex-to-claude-33333"
    tmp_id = "20260716T010003Z-codex-to-claude-44444"
    cur_id = "20260716T010004Z-codex-to-claude-55555"
    ledger_records = [
        {
            "msg_id": both_id,
            "ts": "2026-07-16T01:00:00.500Z",
            "from": "codex",
            "to": "claude",
            "kind": "question",
            "body": "both copy from ledger",
            "lifecycle": "submitted",
            "source": "rt-say",
        },
        {
            "msg_id": ledger_id,
            "ts": "2026-07-16T01:00:01.500Z",
            "from": "codex",
            "to": "claude",
            "kind": "fyi",
            "body": "ledger only",
            "lifecycle": "submitted",
            "source": "rt-say",
        },
    ]
    (state / "messages" / "codex.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in ledger_records)
    )
    write_mail(state, "claude", both_id, "codex", "question", "both copy from maildir")
    write_mail(state, "claude", mail_id, "codex", "directive", "mail only")
    write_mail(state, "claude", tmp_id, "codex", "fyi", "ignore tmp", folder="tmp")
    write_mail(state, "claude", cur_id, "codex", "fyi", "ignore cur", folder="cur")
    ack_id = "20260716T010005Z-claude-to-codex-66666"
    write_mail(state, "codex", ack_id, "claude", "sync-ack", f"refs={mail_id}")

    current = run_tool("rt-inbox", "claude", "-f", "json", cwd=project)
    assert current.returncode == 0, current.stderr
    current_payload = json.loads(current.stdout)
    assert {record["msg_id"] for record in current_payload} == {both_id, ledger_id}
    assert len(current_payload) == 3

    all_proc = run_tool("rt-inbox", "claude", "--all", "-f", "json", cwd=project)
    assert all_proc.returncode == 0, all_proc.stderr
    payload = json.loads(all_proc.stdout)
    assert len(payload) == 4
    by_id = {}
    for record in payload:
        by_id.setdefault(record["msg_id"], []).append(record)
    assert set(by_id) == {both_id, ledger_id, mail_id}
    both_records = {record["delivery_source"]: record for record in by_id[both_id]}
    assert set(both_records) == {"ledger", "maildir"}
    assert both_records["ledger"]["body"] == "both copy from ledger"
    assert both_records["ledger"]["source"] == "rt-say"
    assert both_records["ledger"]["lifecycle"] == "submitted"
    assert both_records["maildir"]["body"] == "both copy from maildir"
    assert both_records["maildir"]["source"] == "maildir"
    assert both_records["maildir"]["lifecycle"] == "new"
    assert by_id[ledger_id][0]["delivery_source"] == "ledger"
    assert by_id[mail_id][0]["delivery_source"] == "maildir"

    text_proc = run_tool("rt-inbox", "claude", "--all", cwd=project)
    assert text_proc.returncode == 0, text_proc.stderr
    assert "[ledger]" in text_proc.stdout
    assert "[maildir]" in text_proc.stdout
    assert "both copy from ledger" in text_proc.stdout
    assert "both copy from maildir" in text_proc.stdout
    assert tmp_id not in text_proc.stdout
    assert cur_id not in text_proc.stdout


def test_roundtable_gitignore_template_excludes_maildir_inbox():
    assert "inbox/" in (ROOT / "templates" / "roundtable-gitignore.tmpl").read_text().splitlines()


def test_rt_say_maildir_self_ignores_inbox_for_existing_git_projects(tmp_path):
    project, state, env, _trace_dir = say_project(tmp_path)
    env["RT_FROM"] = "codex"
    ignore_path = state / "inbox" / ".gitignore"
    ignore_path.parent.mkdir(parents=True)
    ignore_path.write_text("")
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    proc = run_tool(
        "rt-say",
        "--no-nudge",
        "claude",
        "fyi",
        "git hygiene",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    msg_id = proc.stdout.strip().split()[-1]
    mail_path = state / "inbox" / "claude" / "new" / f"{msg_id}.md"
    assert ignore_path.read_text() == "*\n"
    relative_mail = mail_path.relative_to(project)
    for relative_path in (relative_mail, Path(".roundtable/inbox/.gitignore")):
        ignore_proc = subprocess.run(
            ["git", "check-ignore", "-q", str(relative_path)],
            cwd=project,
            check=False,
        )
        assert ignore_proc.returncode == 0


def test_rt_refresh_bind_persists_explicit_workspace(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    bound = workspace("workspace:9", "Unrelated Workspace", "surface:9", "pane:9", "Codex bound")
    other = workspace("workspace:2", "Other", "surface:2", "pane:2", "Other")
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(other, bound),
        surface_workspace=bound,
    )

    proc = run_tool("rt-refresh", "--bind", "workspace:9", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_ref"] == "workspace:9"
    assert runtime["workspace_id"] == "uuid-workspace:9"
    assert runtime["workspace_binding"]["workspace_id"] == "uuid-workspace:9"
    assert runtime["workspace_binding"]["ref"] == "workspace:9"
    assert runtime["workspace_binding"]["title"] == "Unrelated Workspace"


def test_rt_refresh_fails_closed_when_surface_list_returns_focused_workspace(tmp_path):
    project = tmp_path / "project"
    bound = workspace(
        "workspace:1",
        "Roundtable",
        "surface:1",
        "pane:1",
        "Codex",
        workspace_id="UUID-A",
    )
    focused = workspace(
        "workspace:4",
        "Quant",
        "surface:4",
        "pane:4",
        "Claude",
        workspace_id="UUID-B",
    )
    existing_runtime = bound_runtime(
        project,
        "workspace:1",
        "UUID-A",
        title="Roundtable",
        surface_ref="surface:1",
        pane_ref="pane:1",
    )
    existing_runtime["workspace_binding"].pop("workspace_id")
    state = write_project(project, runtime=existing_runtime)
    runtime_path = state / "runtime.json"
    before = runtime_path.read_bytes()
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(bound, focused),
        identify={
            "caller": None,
            "focused": {
                "workspace_ref": "workspace:4",
                "workspace_id": "UUID-B",
                "surface_ref": "surface:4",
            },
        },
        screens={"surface:1": "Claude Code reviewing OpenAI Codex"},
        surface_list=[
            {
                **focused["panes"][0]["surfaces"][0],
                "resume_binding": {"kind": "claude", "updated_at": 1},
            }
        ],
    )

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode != 0
    assert "surface.list returned a different workspace" in proc.stderr
    assert "refusing to rewrite runtime" in proc.stderr
    assert runtime_path.read_bytes() == before
    runtime = json.loads(before)
    assert runtime["workspace_ref"] == "workspace:1"
    assert runtime["workspace_id"] == "UUID-A"
    assert runtime["workspace_binding"]["ref"] == "workspace:1"
    assert runtime["workspace_binding"]["source"] == "existing"
    assert runtime["caller"] == {}
    assert runtime["agents"]["codex"]["surface_ref"] == "surface:1"
    assert all(agent["surface_ref"] != "surface:4" for agent in runtime["agents"].values())


def test_rt_refresh_real_caller_can_rebind_existing_project(tmp_path):
    project = tmp_path / "project"
    old = workspace(
        "workspace:1",
        "Roundtable",
        "surface:1",
        "pane:1",
        "Codex",
        workspace_id="UUID-A",
    )
    caller_workspace = workspace(
        "workspace:4",
        "Moved Roundtable",
        "surface:4",
        "pane:4",
        "Codex",
        workspace_id="UUID-B",
    )
    state = write_project(
        project,
        runtime=bound_runtime(project, "workspace:1", "UUID-A", title="Roundtable"),
    )
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(old, caller_workspace),
        identify={
            "caller": {
                "workspace_ref": "workspace:4",
                "workspace_id": "UUID-B",
                "surface_ref": "surface:4",
            }
        },
    )

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_ref"] == "workspace:4"
    assert runtime["workspace_id"] == "UUID-B"
    assert runtime["workspace_binding"]["workspace_id"] == "UUID-B"
    assert runtime["workspace_binding"]["source"] == "caller-rebind"
    assert "rebinding to workspace:4" in proc.stderr


def test_rt_refresh_follows_workspace_uuid_when_ordinal_ref_drifts(tmp_path):
    project = tmp_path / "project"
    reused_ref = workspace(
        "workspace:1",
        "Other",
        "surface:1",
        "pane:1",
        "Claude",
        workspace_id="UUID-B",
    )
    moved = workspace(
        "workspace:9",
        "Roundtable",
        "surface:9",
        "pane:9",
        "Codex",
        workspace_id="UUID-A",
    )
    existing_runtime = bound_runtime(
        project,
        "workspace:1",
        "UUID-A",
        title="Roundtable",
        surface_ref="surface:9",
        pane_ref="pane:9",
    )
    existing_runtime["workspace_binding"].pop("workspace_id")
    state = write_project(project, runtime=existing_runtime)
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(reused_ref, moved),
        identify={"caller": None},
        surface_workspace=moved,
    )

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_id"] == "UUID-A"
    assert runtime["workspace_ref"] == "workspace:9"
    assert runtime["workspace_binding"]["workspace_id"] == "UUID-A"
    assert runtime["workspace_binding"]["ref"] == "workspace:9"
    assert runtime["workspace_binding"]["source"] == "existing"


def test_rt_refresh_does_not_fallback_to_reused_ref_when_uuid_is_missing(tmp_path):
    project = tmp_path / "project"
    reused_ref = workspace(
        "workspace:1",
        "Other",
        "surface:1",
        "pane:1",
        "Claude",
        workspace_id="UUID-B",
    )
    state = write_project(
        project,
        runtime=bound_runtime(project, "workspace:1", "UUID-A", title="Roundtable"),
    )
    runtime_path = state / "runtime.json"
    before = runtime_path.read_bytes()
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(reused_ref),
        identify={"caller": None, "focused": {"workspace_ref": "workspace:1", "workspace_id": "UUID-B"}},
    )

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode != 0
    assert "stored workspace UUID binding not found: UUID-A" in proc.stderr
    assert runtime_path.read_bytes() == before


def test_rt_refresh_legacy_ref_binding_upgrades_to_workspace_uuid(tmp_path):
    project = tmp_path / "project"
    bound = workspace(
        "workspace:1",
        "Roundtable",
        "surface:1",
        "pane:1",
        "Codex",
        workspace_id="UUID-A",
    )
    state = write_project(
        project,
        runtime=bound_runtime(
            project,
            "workspace:1",
            title="Roundtable",
            surface_ref="surface:1",
            pane_ref="pane:1",
        ),
    )
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(bound),
        identify={"caller": None},
    )

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_id"] == "UUID-A"
    assert runtime["workspace_binding"]["workspace_id"] == "UUID-A"
    assert runtime["workspace_binding"]["ref"] == "workspace:1"
    assert runtime["workspace_binding"]["source"] == "existing"


def workspace_with_review_surfaces():
    return {
        "id": "uuid-workspace:4",
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
            "surface:23": "Claude Code",
            "surface:24": "OpenAI Codex",
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


def test_rt_refresh_bind_current_requires_real_caller_and_does_not_write_runtime(tmp_path):
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

    assert proc.returncode != 0
    assert "requires a real cmux caller" in proc.stderr
    assert not (state / "runtime.json").exists()


def test_rt_refresh_bind_current_uses_real_caller_workspace(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    caller_workspace = workspace(
        "workspace:4",
        "Roundtable",
        "surface:4",
        "pane:4",
        "Codex",
        workspace_id="UUID-A",
    )
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(caller_workspace),
        identify={
            "caller": {
                "workspace_ref": "workspace:4",
                "workspace_id": "UUID-A",
                "surface_ref": "surface:4",
            }
        },
    )

    proc = run_tool("rt-refresh", "--bind-current", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    runtime = json.loads((state / "runtime.json").read_text())
    assert runtime["workspace_id"] == "UUID-A"
    assert runtime["workspace_ref"] == "workspace:4"
    assert runtime["workspace_binding"]["workspace_id"] == "UUID-A"
    assert runtime["workspace_binding"]["source"] == "--bind-current"


def test_rt_refresh_without_caller_or_stored_binding_fails_without_state(tmp_path):
    project = tmp_path / "project"
    state = write_project(project, workspace_title="Configured Workspace")
    configured = workspace("workspace:5", "Configured Workspace", "surface:5", "pane:5", "Codex")
    other = workspace("workspace:6", "project", "surface:6", "pane:6", "Other")
    env = fake_cmux(tmp_path, tree=tree_with_workspaces(other, configured))

    proc = run_tool("rt-refresh", cwd=project, env=env)

    assert proc.returncode != 0
    assert "no real cmux caller and no stored workspace binding" in proc.stderr
    assert not (state / "runtime.json").exists()


def test_roundtable_init_next_steps_include_binding_and_watcher(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()

    proc = run_tool("roundtable-init", "--no-git", "-p", str(parent), "sample")

    assert proc.returncode == 0, proc.stderr
    assert "rt-refresh --bind-current" in proc.stdout
    assert "rt-watch-ensure" in proc.stdout


def test_rt_watch_ensure_does_not_use_focused_workspace_without_caller(tmp_path):
    project = tmp_path / "project"
    state = write_project(project)
    focused = workspace("workspace:4", "Other", "surface:4", "pane:4", "Terminal")
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(focused),
        identify={
            "caller": None,
            "focused": {"workspace_ref": "workspace:4", "pane_ref": "pane:4"},
        },
    )

    proc = run_executable("rt-watch-ensure", cwd=project, env=env)

    assert proc.returncode == 0
    assert "SCRIPT_DIR" not in proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "no cmux caller workspace/pane" in (state / "rt-watch.log").read_text()


def test_sync_ack_uses_quiet_ack_filename_without_changing_header_id(tmp_path):
    project, state, env, _trace_dir = say_project(tmp_path)
    env.update(
        {
            "RT_FROM": "claude",
            "CMUX_FAKE_IDENTIFY": json.dumps(
                {
                    "caller": {
                        "workspace_ref": "workspace:1",
                        "surface_ref": "surface:2",
                    }
                }
            ),
        }
    )
    original = "20260717T010000Z-codex-to-claude-original"

    proc = run_tool("rt-ack", original, "received", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    ack_id = proc.stdout.strip().split()[-1]
    path = state / "inbox" / "codex" / "new" / f"ack-{ack_id}.md"
    assert path.is_file()
    assert path.read_text().startswith(f"[CLAUDE→CODEX sync-ack id={ack_id}]")


def test_per_agent_delivery_retires_claude_nudge_but_keeps_hermes_dual(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    agents_path.write_text(
        agents_path.read_text().replace(
            "  claude:\n    harness: claude-code\n",
            "  claude:\n    harness: claude-code\n    delivery: maildir\n",
        )
    )
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    hermes_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:3",
        "pane_ref": "pane:3",
        "status": "idle",
    }
    runtime["agents"]["hermes"] = hermes_route
    runtime["surfaces"].append(hermes_route)
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env["RT_FROM"] = "codex"

    claude = run_tool("rt-say", "claude", "fyi", "quiet", cwd=project, env=env)
    hermes = run_tool("rt-say", "hermes", "fyi", "dual", cwd=project, env=env)

    assert claude.returncode == 0, claude.stderr
    assert claude.stdout.startswith("sent maildir-only ")
    assert hermes.returncode == 0, hermes.stderr
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1
    claude_records = [
        record for record in read_ledger(state) if record["to"] == "claude"
    ]
    assert [record["lifecycle"] for record in claude_records] == ["pending"]


def test_configured_claude_instances_inherit_maildir_delivery(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    text = agents_path.read_text().replace(
        "  claude:\n    harness: claude-code\n",
        "  claude:\n    harness: claude-code\n    delivery: maildir\n",
    )
    text = text.replace(
        "    instances:\n      - id: claude\n        session_id: null\n",
        "    instances:\n      - id: claude-build\n      - id: claude-review\n",
    )
    agents_path.write_text(text)
    env["RT_FROM"] = "codex"

    proc = run_tool(
        "rt-say", "claude-build", "fyi", "instance quiet", cwd=project, env=env
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("sent maildir-only ")
    calls = read_cmux_calls(trace_dir)
    assert [call for call in calls if call[:1] in (["send"], ["send-key"])] == []


def test_maildir_sender_uses_unique_codex_thread_environment_without_cmux(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    agents_path.write_text(
        agents_path.read_text().replace(
            "  claude:\n    harness: claude-code\n",
            "  claude:\n    harness: claude-code\n    delivery: maildir\n",
        )
    )
    (state / "runtime.json").unlink()
    env.update(
        {
            "RT_FROM": "",
            "CODEX_THREAD_ID": "thread-from-app-server",
            "CMUX_FAKE_IDENTIFY": json.dumps({"caller": None}),
        }
    )

    proc = run_tool("rt-say", "claude", "fyi", "remote turn", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("sent maildir-only ")
    assert [record["from"] for record in read_ledger(state)] == ["codex"]
    assert read_cmux_calls(trace_dir) == []


def test_maildir_sender_keeps_live_caller_authority_over_stale_environment(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    agents_path.write_text(
        agents_path.read_text().replace(
            "  claude:\n    harness: claude-code\n",
            "  claude:\n    harness: claude-code\n    delivery: maildir\n",
        )
    )
    env.update(
        {
            "RT_FROM": "codex",
            "CODEX_THREAD_ID": "stale-codex-thread",
            "CMUX_FAKE_IDENTIFY": json.dumps(
                {
                    "caller": {
                        "workspace_ref": "workspace:1",
                        "workspace_id": "UUID-A",
                        "surface_ref": "surface:2",
                    }
                }
            ),
        }
    )

    proc = run_tool("rt-say", "claude", "fyi", "self send", cwd=project, env=env)

    assert proc.returncode != 0
    assert "refusing self-send from claude to claude" in proc.stderr
    assert not (state / "inbox").exists()
    assert read_ledger(state, sender="codex") == []
    calls = read_cmux_calls(trace_dir)
    assert [call for call in calls if call[:1] in (["send"], ["send-key"])] == []


def test_dual_sender_uses_unique_codex_thread_environment_without_cmux(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    hermes_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:3",
        "pane_ref": "pane:3",
        "status": "idle",
    }
    runtime["agents"]["hermes"] = hermes_route
    runtime["surfaces"].append(hermes_route)
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env.update(
        {
            "RT_FROM": "",
            "CODEX_THREAD_ID": "thread-from-app-server",
            "CMUX_FAKE_IDENTIFY": json.dumps({"caller": None}),
        }
    )

    proc = run_tool("rt-say", "hermes", "fyi", "remote dual", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    assert [record["from"] for record in read_ledger(state)] == [
        "codex",
        "codex",
        "codex",
    ]
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1


def test_dual_sender_keeps_live_caller_authority_over_stale_environment(tmp_path):
    project, state, env, _trace_dir = say_project(tmp_path)
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    hermes_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:3",
        "pane_ref": "pane:3",
        "status": "idle",
    }
    runtime["agents"]["hermes"] = hermes_route
    runtime["surfaces"].append(hermes_route)
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env.update(
        {
            "RT_FROM": "codex",
            "CODEX_THREAD_ID": "stale-codex-thread",
            "CMUX_FAKE_IDENTIFY": json.dumps(
                {
                    "caller": {
                        "workspace_ref": "workspace:1",
                        "workspace_id": "UUID-A",
                        "surface_ref": "surface:2",
                    }
                }
            ),
        }
    )

    proc = run_tool("rt-say", "hermes", "fyi", "caller wins", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    assert read_ledger(state, sender="codex") == []
    assert [record["from"] for record in read_ledger(state, sender="claude")] == [
        "claude",
        "claude",
        "claude",
    ]


def test_dual_rt_ack_uses_unique_codex_thread_environment_without_cmux(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    runtime_path = state / "runtime.json"
    runtime = json.loads(runtime_path.read_text())
    hermes_route = {
        "workspace_ref": "workspace:1",
        "surface_ref": "surface:3",
        "pane_ref": "pane:3",
        "status": "idle",
    }
    runtime["agents"]["hermes"] = hermes_route
    runtime["surfaces"].append(hermes_route)
    runtime_path.write_text(json.dumps(runtime, indent=2) + "\n")
    env.update(
        {
            "RT_FROM": "",
            "CODEX_THREAD_ID": "thread-from-app-server",
            "CMUX_FAKE_IDENTIFY": json.dumps({"caller": None}),
        }
    )
    original = "20260717T010000Z-hermes-to-codex-original"

    proc = run_tool("rt-ack", original, "remote ack", cwd=project, env=env)

    assert proc.returncode == 0, proc.stderr
    ack_id = proc.stdout.strip().split()[-1]
    ack_path = state / "inbox" / "hermes" / "new" / f"ack-{ack_id}.md"
    assert ack_path.is_file()
    assert ack_path.read_text().startswith(f"[CODEX→HERMES sync-ack id={ack_id}]")
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1


def test_legacy_only_explicitly_overrides_maildir_delivery(tmp_path):
    project, state, env, trace_dir = say_project(tmp_path)
    agents_path = state / "agents.yaml"
    agents_path.write_text(
        agents_path.read_text().replace(
            "  claude:\n    harness: claude-code\n",
            "  claude:\n    harness: claude-code\n    delivery: maildir\n",
        )
    )

    proc = run_tool(
        "rt-say",
        "--legacy-nudge-only",
        "claude",
        "fyi",
        "manual fallback",
        cwd=project,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (state / "inbox").exists()
    calls = read_cmux_calls(trace_dir)
    assert len([call for call in calls if call[:1] == ["send"]]) == 1
    assert len([call for call in calls if call[:1] == ["send-key"]]) == 1


def test_startup_advisory_suggests_unique_same_workspace_project(tmp_path):
    peer = tmp_path / "peer"
    write_project(peer)
    outside = tmp_path / "outside"
    outside.mkdir()
    current_id = "current-surface-uuid"
    active = workspace(
        "workspace:1",
        "project",
        "surface:1",
        "pane:1",
        "Claude",
        workspace_id="workspace-uuid",
    )
    surface_list = [
        {
            "id": current_id,
            "ref": "surface:1",
            "type": "terminal",
            "requested_working_directory": str(outside),
        },
        {
            "id": "peer-surface-uuid",
            "ref": "surface:2",
            "type": "terminal",
            "requested_working_directory": str(peer),
        },
    ]
    env = fake_cmux(
        tmp_path,
        tree=tree_with_workspaces(active),
        identify={
            "caller": {
                "workspace_ref": "workspace:1",
                "workspace_id": "workspace-uuid",
                "surface_ref": "surface:1",
                "surface_id": current_id,
            }
        },
        surface_list=surface_list,
        surface_workspace=active,
    )
    env.update({"CMUX_SURFACE_ID": current_id, "ROUNDTABLE_PROJECT_DIR": ""})

    proc = run_executable("rt-watch-ensure", cwd=outside, env=env)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("\n") == 1
    assert "cwd 不在 roundtable 项目" in proc.stdout
    assert str(peer.resolve()) in proc.stdout
    assert "export ROUNDTABLE_PROJECT_DIR=" in proc.stdout


def test_startup_advisory_without_cmux_environment_is_silent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    proc = run_executable(
        "rt-watch-ensure",
        cwd=outside,
        env={"CMUX_SURFACE_ID": "", "ROUNDTABLE_PROJECT_DIR": ""},
    )

    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
