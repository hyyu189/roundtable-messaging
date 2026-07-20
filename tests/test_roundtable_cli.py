import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))


def load_script():
    loader = importlib.machinery.SourceFileLoader(
        "roundtable_unified_cli", str(BIN / "roundtable")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


roundtable = load_script()


class TTYInput(io.StringIO):
    def isatty(self):
        return True


def write_project(path: Path, seats=None) -> Path:
    seats = seats or {"codex": ("codex", ["codex"])}
    state = path / ".roundtable"
    state.mkdir(parents=True)
    lines = [
        "schema: roundtable.agents.v1",
        f"project: {path.resolve()}",
        "agents:",
    ]
    for name, (harness, instance_ids) in seats.items():
        lines.extend(
            [
                f"  {name}:",
                f"    harness: {harness}",
                "    instances:",
            ]
        )
        lines.extend(f"      - id: {instance_id}" for instance_id in instance_ids)
    (state / "agents.yaml").write_text("\n".join(lines) + "\n")
    return path.resolve()


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / "projects.yaml"
    monkeypatch.setenv("RT_PROJECTS_FILE", str(registry))
    return registry


@pytest.fixture
def fake_commands(monkeypatch, tmp_path):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()

    def resolve(name):
        return command_dir / name

    monkeypatch.setattr(roundtable, "sibling", resolve)
    return command_dir


@pytest.mark.parametrize(
    ("alias", "target"),
    sorted(roundtable.ALIASES.items()),
)
def test_scriptable_aliases_pass_every_argument_through(
    alias, target, fake_commands, tmp_path
):
    calls = []

    def fake_exec(path, argv):
        calls.append((path, argv))
        return 0

    result = roundtable.main(
        [alias, "--example", "two words"],
        cwd=tmp_path,
        home=tmp_path / "home",
        exec_runner=fake_exec,
    )

    expected = fake_commands / target
    assert result == 0
    assert calls == [
        (
            str(expected),
            [str(expected), "--example", "two words"],
        )
    ]


def test_no_argument_non_tty_fails_with_help_without_exec(fake_commands, tmp_path):
    stderr = io.StringIO()
    calls = []

    result = roundtable.main(
        [],
        cwd=tmp_path,
        home=tmp_path / "home",
        stdin=io.StringIO(),
        stderr=stderr,
        exec_runner=lambda *args: calls.append(args),
    )

    assert result == 2
    assert "stdin is not a TTY" in stderr.getvalue()
    assert "usage: roundtable" in stderr.getvalue()
    assert calls == []


def test_anchored_project_goes_directly_to_configured_seat_selector(
    tmp_path, isolated_registry, fake_commands
):
    project = write_project(
        tmp_path / "project",
        {
            "claude": ("claude-code", ["claude"]),
            "codex": ("codex", ["codex-a", "codex-b"]),
            "hermes": ("hermes-agent", ["hermes"]),
        },
    )
    nested = project / "nested"
    nested.mkdir()
    stderr = io.StringIO()
    environment = {}
    exec_calls = []
    chdir_calls = []

    result = roundtable.main(
        [],
        cwd=nested,
        home=tmp_path / "home",
        stdin=TTYInput("3\n"),
        stderr=stderr,
        environ=environment,
        exec_runner=lambda path, argv: exec_calls.append((path, argv)) or 0,
        chdir_runner=chdir_calls.append,
    )

    assert result == 0
    assert f"Roundtable project: {project}" in stderr.getvalue()
    assert "Choose a Roundtable project:" not in stderr.getvalue()
    assert "codex — codex-b" in stderr.getvalue()
    assert environment["RT_FROM"] == "codex-b"
    assert chdir_calls == [project]
    expected = fake_commands / "rt-codex"
    assert exec_calls == [(str(expected), [str(expected)])]


def test_onboarding_can_safely_set_up_current_folder_without_git(
    tmp_path, isolated_registry, fake_commands
):
    folder = tmp_path / "existing"
    folder.mkdir()
    (folder / "README.md").write_text("# User file\n")
    init_calls = []

    def fake_init(command, cwd, check):
        init_calls.append((command, cwd, check))
        assert "--git" not in command
        write_project(cwd)
        return SimpleNamespace(returncode=0)

    environment = {}
    result = roundtable.main(
        [],
        cwd=folder,
        home=tmp_path / "home",
        stdin=TTYInput("1\n\n1\n"),
        stderr=io.StringIO(),
        environ=environment,
        init_runner=fake_init,
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 0
    assert init_calls == [
        (
            [str(fake_commands / "roundtable-init"), "--here"],
            folder.resolve(),
            False,
        )
    ]
    assert (folder / "README.md").read_text() == "# User file\n"
    assert environment["RT_FROM"] == "codex"


def test_onboarding_can_set_up_another_existing_folder(
    tmp_path, isolated_registry, fake_commands
):
    cwd = tmp_path / "start"
    other = tmp_path / "other"
    cwd.mkdir()
    other.mkdir()
    init_calls = []

    def fake_init(command, cwd, check):
        init_calls.append((command, cwd, check))
        write_project(cwd)
        return SimpleNamespace(returncode=0)

    result = roundtable.main(
        [],
        cwd=cwd,
        home=tmp_path / "home",
        stdin=TTYInput(f"2\n{other}\n\n1\n"),
        stderr=io.StringIO(),
        environ={},
        init_runner=fake_init,
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 0
    assert init_calls[0][0] == [
        str(fake_commands / "roundtable-init"),
        "--here",
    ]
    assert init_calls[0][1] == other.resolve()


def test_onboarding_creates_new_folder_and_only_passes_git_after_yes(
    tmp_path, isolated_registry, fake_commands
):
    cwd = tmp_path / "start"
    cwd.mkdir()
    init_calls = []

    def fake_init(command, cwd, check):
        init_calls.append((command, cwd, check))
        parent = Path(command[command.index("--parent") + 1])
        write_project(parent / command[1])
        return SimpleNamespace(returncode=0)

    result = roundtable.main(
        [],
        cwd=cwd,
        home=tmp_path / "home",
        stdin=TTYInput("3\n\nnew-project\nyes\n1\n"),
        stderr=io.StringIO(),
        environ={},
        init_runner=fake_init,
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 0
    assert init_calls == [
        (
            [
                str(fake_commands / "roundtable-init"),
                "new-project",
                "--parent",
                str(cwd.resolve()),
                "--git",
            ],
            cwd.resolve(),
            False,
        )
    ]


def test_home_is_never_offered_as_the_current_project(
    tmp_path, isolated_registry, fake_commands
):
    home = tmp_path / "home"
    home.mkdir()
    stderr = io.StringIO()

    result = roundtable.main(
        [],
        cwd=home,
        home=home,
        stdin=TTYInput("9\n"),
        stderr=stderr,
        environ={},
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 2
    assert "Set up this folder safely" not in stderr.getvalue()
    assert "Set up another existing folder" in stderr.getvalue()
    assert "Create a new folder" in stderr.getvalue()


def test_zero_is_not_accepted_as_a_menu_selection(
    tmp_path, isolated_registry, fake_commands
):
    folder = tmp_path / "folder"
    folder.mkdir()
    stderr = io.StringIO()

    result = roundtable.main(
        [],
        cwd=folder,
        home=tmp_path / "home",
        stdin=TTYInput("0\n"),
        stderr=stderr,
        environ={},
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 2
    assert "invalid project selection" in stderr.getvalue()


def test_registered_project_can_be_selected_without_reinitializing(
    tmp_path, isolated_registry, fake_commands
):
    project = write_project(tmp_path / "registered")
    isolated_registry.write_text(
        json.dumps(
            {
                "schema": "roundtable.projects.v1",
                "projects": [
                    {
                        "root": str(project),
                        "registered_at": "2026-07-19T00:00:00Z",
                    }
                ],
            }
        )
        + "\n"
    )
    cwd = tmp_path / "outside"
    cwd.mkdir()
    init_calls = []

    result = roundtable.main(
        [],
        cwd=cwd,
        home=tmp_path / "home",
        stdin=TTYInput("1\n1\n"),
        stderr=io.StringIO(),
        environ={},
        init_runner=lambda *args, **kwargs: init_calls.append((args, kwargs)),
        exec_runner=lambda *_: 0,
        chdir_runner=lambda _: None,
    )

    assert result == 0
    assert init_calls == []
