from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roundtable_packaging import migrate


def _write(path: Path, payload: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    path.chmod(mode)


def _git(prefix: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(prefix), *arguments], check=True)


def _plist(home: Path, prefix: Path, label: str) -> dict:
    runtime = prefix / ".runtime"
    socket = home / ".codex" / "app-server-control" / "app-server-control.sock"
    path_value = ":".join(
        [
            str(home / ".npm-global" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )
    environment = {
        "HOME": str(home),
        "PATH": path_value,
        "CODEX_HOME": str(home / ".codex"),
        "RT_CODEX_RUNTIME_DIR": str(runtime),
    }
    if label == migrate.CODEX_LABELS[0]:
        executable = home / ".npm-global" / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        arguments = [str(executable), "app-server", "--listen", f"unix://{socket}"]
        keep_alive: object = True
        stem = "codex-app-server"
    else:
        arguments = [str(prefix / "bin" / "rt-codex-wake"), "--socket", str(socket), "run"]
        keep_alive = {"SuccessfulExit": False}
        stem = "rt-codex-wake"
    return {
        "Label": label,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": keep_alive,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "WorkingDirectory": str(home),
        "EnvironmentVariables": environment,
        "StandardOutPath": str(runtime / f"{stem}.stdout.log"),
        "StandardErrorPath": str(runtime / f"{stem}.stderr.log"),
    }


@pytest.fixture
def legacy_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    prefix = home / ".roundtable"
    link_dir = home / ".local" / "bin"
    home.mkdir()
    prefix.mkdir()

    programs = {
        "_rtlib.py": (
            '# legacy library\nPROJECTS_SCHEMA = "roundtable.projects.v1"\n'
        ),
        "roundtable-init": "#!/bin/sh\n# roundtable legacy init\n",
        "rt-say": (
            "#!/usr/bin/env python3\n"
            "from _rtlib import find_project_root, load_agents_doc\n"
            "# durable maildir sender\n"
        ),
        "rt-inbox": "#!/bin/sh\n# roundtable legacy inbox\n",
        "rt-ack": "#!/bin/sh\n# roundtable legacy ack\n",
        "rt-codex-wake": "#!/bin/sh\n# roundtable legacy wake\n",
    }
    for name, payload in programs.items():
        _write(prefix / "bin" / name, payload, 0o755)
    _write(prefix / "skills" / "shared" / "roundtable" / "SKILL.md", "# Roundtable\n", 0o600)
    (prefix / "bin" / "__pycache__").mkdir()
    _write(prefix / "bin" / "__pycache__" / "ignored.pyc", "cache")

    _git(prefix, "init", "-q")
    _git(prefix, "config", "user.email", "test@example.invalid")
    _git(prefix, "config", "user.name", "Roundtable test")
    _git(prefix, "add", "bin", "skills/shared/roundtable/SKILL.md")
    _git(prefix, "commit", "-qm", "legacy roundtable")

    # Durable and unrelated state is deliberately outside the Git-backed
    # program set and must survive apply byte-for-byte.
    _write(prefix / "projects.yaml", "schema: roundtable.projects.v1\nprojects: []\n", 0o600)
    _write(prefix / ".runtime" / "projects" / "abc" / "lease.json", '{"lease": 7}\n', 0o600)
    (prefix / ".runtime").chmod(0o755)
    (prefix / ".runtime" / "projects").chmod(0o755)
    _write(prefix / "docs" / "personal-note.md", "keep me\n")

    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    monkeypatch.setenv("RT_LAUNCH_AGENTS_DIR", str(launch_agents))
    for label in migrate.CODEX_LABELS:
        path = launch_agents / f"{label}.plist"
        path.write_bytes(plistlib.dumps(_plist(home, prefix, label), sort_keys=True))
        path.chmod(0o600)

    link_dir.mkdir(parents=True)
    (link_dir / "rt-say").symlink_to("../../.roundtable/bin/rt-say")

    launchctl = tmp_path / "launchctl"
    _write(
        launchctl,
        "#!/bin/sh\n"
        "printf '%s\\n' 'Bad request.' 'Could not find service test in domain' >&2\n"
        "exit 113\n",
        0o755,
    )
    monkeypatch.setenv("RT_LAUNCHCTL", str(launchctl))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return home, prefix, link_dir


def _run(
    capsys: pytest.CaptureFixture[str],
    home: Path,
    prefix: Path,
    link_dir: Path,
    command: str,
) -> tuple[int, dict]:
    code = migrate.main(
        [
            command,
            "--home",
            str(home),
            "--prefix",
            str(prefix),
            "--link-dir",
            str(link_dir),
            "--json",
        ]
    )
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def test_plan_is_read_only_and_scoped(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    registry = (prefix / "projects.yaml").read_bytes()
    lease = (prefix / ".runtime" / "projects" / "abc" / "lease.json").read_bytes()
    head_before = subprocess.check_output(
        ["git", "-C", str(prefix), "status", "--porcelain=v1"], text=True
    )

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 0
    assert result["state"] == "ready"
    assert result["writes"] is False
    assert result["launchctl_invoked"] is False
    paths = {Path(item["path"]) for item in result["actions"]}
    assert prefix / "bin" / "rt-say" in paths
    assert prefix / "skills" / "shared" / "roundtable" / "SKILL.md" in paths
    assert home / "Library" / "LaunchAgents" / "com.roundtable.codex-wake.plist" in paths
    assert link_dir / "rt-say" in paths
    assert prefix / ".runtime" in paths
    assert prefix / ".runtime" / "projects" in paths
    mode_actions = {
        Path(item["path"]): item
        for item in result["actions"]
        if item["kind"] == "directory-mode"
    }
    assert mode_actions[prefix / ".runtime"]["mode"] == 0o755
    assert mode_actions[prefix / ".runtime"]["desired_mode"] == 0o700
    assert not (prefix / migrate.MANIFEST_NAME).exists()
    assert not (prefix / "backups").exists()
    assert (prefix / "projects.yaml").read_bytes() == registry
    assert (prefix / ".runtime" / "projects" / "abc" / "lease.json").read_bytes() == lease
    assert subprocess.check_output(
        ["git", "-C", str(prefix), "status", "--porcelain=v1"], text=True
    ) == head_before


def test_apply_is_idempotent_and_rollback_restores_exact_leaves(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    originals = {
        prefix / "bin" / "rt-say": ((prefix / "bin" / "rt-say").read_bytes(), 0o755),
        prefix / "skills" / "shared" / "roundtable" / "SKILL.md": (
            (prefix / "skills" / "shared" / "roundtable" / "SKILL.md").read_bytes(),
            0o600,
        ),
        home / "Library" / "LaunchAgents" / "com.roundtable.codex-wake.plist": (
            (home / "Library" / "LaunchAgents" / "com.roundtable.codex-wake.plist").read_bytes(),
            0o600,
        ),
    }
    registry = (prefix / "projects.yaml").read_bytes()
    lease = (prefix / ".runtime" / "projects" / "abc" / "lease.json").read_bytes()

    code, applied = _run(capsys, home, prefix, link_dir, "apply")

    assert code == 0
    assert applied["state"] == "applied"
    assert applied["writes"] is True
    assert applied["launchctl_invoked"] is True
    assert (prefix / migrate.MANIFEST_NAME).is_file()
    assert stat.S_IMODE((prefix / migrate.MANIFEST_NAME).stat().st_mode) == 0o600
    for item in applied["actions"]:
        if item["kind"] == "directory-mode":
            assert stat.S_IMODE(Path(item["path"]).stat().st_mode) == 0o700
            assert item["backup"] is None
        else:
            assert not os.path.lexists(item["path"])
            assert os.path.lexists(item["backup"])
    assert (prefix / "bin" / "__pycache__" / "ignored.pyc").read_text() == "cache"
    assert (prefix / "docs" / "personal-note.md").read_text() == "keep me\n"
    assert (prefix / "projects.yaml").read_bytes() == registry
    assert (prefix / ".runtime" / "projects" / "abc" / "lease.json").read_bytes() == lease

    code, repeated = _run(capsys, home, prefix, link_dir, "apply")
    assert code == 0
    assert repeated["writes"] is False
    assert repeated["launchctl_invoked"] is False

    code, rolled_back = _run(capsys, home, prefix, link_dir, "rollback")
    assert code == 0
    assert rolled_back["state"] == "rolled-back"
    assert rolled_back["writes"] is True
    assert rolled_back["launchctl_invoked"] is True
    for path, (payload, mode) in originals.items():
        assert path.read_bytes() == payload
        assert stat.S_IMODE(path.stat().st_mode) == mode
    assert (link_dir / "rt-say").is_symlink()
    assert os.readlink(link_dir / "rt-say") == "../../.roundtable/bin/rt-say"
    assert stat.S_IMODE((prefix / ".runtime").stat().st_mode) == 0o755
    assert stat.S_IMODE((prefix / ".runtime" / "projects").stat().st_mode) == 0o755
    assert (prefix / ".runtime" / "projects" / "abc" / "lease.json").read_bytes() == lease

    manifest_before = (prefix / migrate.MANIFEST_NAME).read_bytes()
    code, repeated_rollback = _run(capsys, home, prefix, link_dir, "rollback")
    assert code == 0
    assert repeated_rollback["writes"] is False
    assert repeated_rollback["launchctl_invoked"] is False
    assert (prefix / migrate.MANIFEST_NAME).read_bytes() == manifest_before


def test_applied_legacy_migration_can_be_replaced_by_managed_installer(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    registry = (prefix / "projects.yaml").read_bytes()
    lease_path = prefix / ".runtime" / "projects" / "abc" / "lease.json"
    lease = lease_path.read_bytes()

    code, migrated = _run(capsys, home, prefix, link_dir, "apply")

    assert code == 0, migrated
    environment = os.environ.copy()
    for name in (
        "RT_PROJECTS_FILE",
        "RT_RUNTIME_DIR",
        "RT_CODEX_RUNTIME_DIR",
        "CODEX_HOME",
        "RT_LAUNCH_AGENTS_DIR",
        "RT_LAUNCHCTL",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "HOME": str(home),
            "ROUNDTABLE_BOOTSTRAP_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    installed = subprocess.run(
        [
            str(ROOT / "scripts" / "install.sh"),
            "--prefix",
            str(prefix),
            "--link-dir",
            str(link_dir),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    assert (prefix / "install-manifest.json").is_file()
    assert (prefix / "bin" / "roundtable").is_file()
    assert (link_dir / "roundtable").is_symlink()
    assert (prefix / migrate.MANIFEST_NAME).is_file()
    assert (prefix / "projects.yaml").read_bytes() == registry
    assert lease_path.read_bytes() == lease


@pytest.mark.parametrize("conflict", ["modified", "unknown-bin", "foreign-plist"])
def test_plan_fails_closed_without_writes(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    conflict: str,
) -> None:
    home, prefix, link_dir = legacy_install
    if conflict == "modified":
        _write(prefix / "bin" / "rt-say", "#!/bin/sh\n# customized\n", 0o755)
    elif conflict == "unknown-bin":
        _write(prefix / "bin" / "my-helper", "#!/bin/sh\n", 0o755)
    else:
        path = home / "Library" / "LaunchAgents" / "com.roundtable.codex-wake.plist"
        value = plistlib.loads(path.read_bytes())
        value["EnvironmentVariables"]["CUSTOM"] = "unsafe"
        path.write_bytes(plistlib.dumps(value, sort_keys=True))

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "refusing" in result["error"]
    assert result["writes"] is False
    assert not (prefix / migrate.MANIFEST_NAME).exists()
    assert not (prefix / "backups").exists()


@pytest.mark.parametrize("plist_files_present", [True, False])
def test_apply_refuses_loaded_service_before_writes(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    plist_files_present: bool,
) -> None:
    home, prefix, link_dir = legacy_install
    if not plist_files_present:
        for label in migrate.CODEX_LABELS:
            (home / "Library" / "LaunchAgents" / f"{label}.plist").unlink()
    launchctl = tmp_path / "active-launchctl"
    _write(launchctl, "#!/bin/sh\nprintf 'loaded service\\n'\nexit 0\n", 0o755)
    monkeypatch.setenv("RT_LAUNCHCTL", str(launchctl))

    code, result = _run(capsys, home, prefix, link_dir, "apply")

    assert code == 2
    assert "while services are loaded" in result["error"]
    assert result["writes"] is False
    assert result["launchctl_invoked"] is True
    assert (prefix / "bin" / "rt-say").is_file()
    assert not (prefix / migrate.MANIFEST_NAME).exists()
    assert not (prefix / "backups").exists()


def test_rollback_refuses_new_install_collision_before_restoring_anything(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    code, applied = _run(capsys, home, prefix, link_dir, "apply")
    assert code == 0
    _write(prefix / "bin" / "rt-say", "#!/bin/sh\n# managed replacement\n", 0o755)

    code, result = _run(capsys, home, prefix, link_dir, "rollback")

    assert code == 2
    assert "refusing rollback over a different path" in result["error"]
    assert not (prefix / "bin" / "rt-ack").exists()
    assert (prefix / "bin" / "rt-say").read_text() == "#!/bin/sh\n# managed replacement\n"
    assert json.loads((prefix / migrate.MANIFEST_NAME).read_text())["state"] == "applied"


def test_apply_error_reports_prior_writes_and_launchctl_inspection(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, prefix, link_dir = legacy_install
    original_remove = migrate._remove_item

    def fail_after_first_remove(item: dict, audit: migrate.OperationAudit) -> None:
        original_remove(item, audit)
        raise migrate.MigrationError("injected migration failure")

    monkeypatch.setattr(migrate, "_remove_item", fail_after_first_remove)

    code, result = _run(capsys, home, prefix, link_dir, "apply")

    assert code == 2
    assert result["error"] == "injected migration failure"
    assert result["writes"] is True
    assert result["launchctl_invoked"] is True
    manifest = json.loads((prefix / migrate.MANIFEST_NAME).read_text())
    assert manifest["state"] == "rolled-back"
    assert (prefix / "bin" / "_rtlib.py").is_file()


@pytest.mark.parametrize("plist_files_present", [True, False])
def test_apply_refuses_inside_codex_before_writes(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    plist_files_present: bool,
) -> None:
    home, prefix, link_dir = legacy_install
    if not plist_files_present:
        for label in migrate.CODEX_LABELS:
            (home / "Library" / "LaunchAgents" / f"{label}.plist").unlink()
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-current")

    code, result = _run(capsys, home, prefix, link_dir, "apply")

    assert code == 2
    assert "outside Codex" in result["error"]
    assert result["writes"] is False
    assert result["launchctl_invoked"] is False
    assert not (prefix / migrate.MANIFEST_NAME).exists()
    assert not (prefix / "backups").exists()


def test_plan_refuses_symlink_or_unknown_runtime_mode(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    projects = prefix / ".runtime" / "projects"
    projects.chmod(0o777)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "unsupported legacy runtime directory mode" in result["error"]
    assert stat.S_IMODE(projects.stat().st_mode) == 0o777
    assert not (prefix / "backups").exists()


def test_plan_refuses_symlinked_runtime_directory(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    projects = prefix / ".runtime" / "projects"
    shutil.rmtree(projects)
    outside = home / "foreign-runtime"
    outside.mkdir()
    projects.symlink_to(outside, target_is_directory=True)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "expected directory, found symlink" in result["error"]
    assert projects.is_symlink()
    assert not (prefix / "backups").exists()


def test_plan_refuses_symlinked_skill_parent(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    original = (prefix / "skills" / "shared" / "roundtable" / "SKILL.md").read_bytes()
    shutil.rmtree(prefix / "skills")
    outside = home / "shared-skills"
    _write(outside / "shared" / "roundtable" / "SKILL.md", original.decode("utf-8"), 0o600)
    (prefix / "skills").symlink_to(outside, target_is_directory=True)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "refusing symlink in migration path" in result["error"]
    assert (outside / "shared" / "roundtable" / "SKILL.md").read_bytes() == original
    assert not (prefix / "backups").exists()


def test_plan_refuses_command_link_target_with_symlink_loop(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    command = link_dir / "rt-say"
    command.unlink()
    (link_dir / "loop").symlink_to("loop")
    command.symlink_to("loop/rt-say")

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "unresolvable legacy command link" in result["error"]
    assert command.is_symlink()


def test_git_inspection_ignores_repository_redirect_environment(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home, prefix, link_dir = legacy_install
    foreign = tmp_path / "foreign-git-state"
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_EXEC_PATH",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
    ):
        monkeypatch.setenv(key, str(foreign / key.lower()))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "invalid injected config")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(foreign))

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 0
    assert result["state"] == "ready"
    assert result["writes"] is False


def test_plan_requires_git_top_level_to_equal_prefix(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    foreign = home / "foreign-worktree"
    foreign.mkdir()
    _git(prefix, "config", "core.worktree", str(foreign))

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "Git worktree top level" in result["error"]
    assert result["writes"] is False


def test_plan_refuses_absolute_migration_id_backup_escape(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    home, prefix, link_dir = legacy_install
    outside = tmp_path / "outside-backup-root"
    manifest = {
        "schema": migrate.SCHEMA,
        "state": "rolled-back",
        "legacy_head": "0" * 40,
        "migration_id": str(outside),
        "home": str(home),
        "prefix": str(prefix),
        "link_dir": str(link_dir),
        "backup_root": str(outside),
        "items": [],
        "preserved": [],
    }
    _write(prefix / migrate.MANIFEST_NAME, json.dumps(manifest), 0o600)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "invalid backup metadata" in result["error"]
    assert not outside.exists()


def test_plan_refuses_manifest_item_outside_home(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    home, prefix, _link_dir = legacy_install
    outside_link_dir = tmp_path / "outside-bin"
    outside_link_dir.mkdir()
    outside_path = outside_link_dir / "rt-say"
    migration_id = "0" * 16
    backup_root = prefix / "backups" / "legacy-migration" / migration_id
    manifest = {
        "schema": migrate.SCHEMA,
        "state": "rolled-back",
        "legacy_head": "0" * 40,
        "migration_id": migration_id,
        "home": str(home),
        "prefix": str(prefix),
        "link_dir": str(outside_link_dir),
        "backup_root": str(backup_root),
        "items": [
            {
                "path": str(outside_path),
                "relative_to_home": str(outside_path),
                "backup": str(backup_root / "payload" / "rt-say"),
                "kind": "symlink",
                "reason": "malicious manifest item",
                "mode": None,
                "sha256": "0" * 64,
                "target": "/tmp/foreign-target",
                "desired_mode": None,
            }
        ],
        "preserved": [],
    }
    _write(prefix / migrate.MANIFEST_NAME, json.dumps(manifest), 0o600)

    code, result = _run(capsys, home, prefix, outside_link_dir, "plan")

    assert code == 2
    assert "escapes home" in result["error"]
    assert list(outside_link_dir.iterdir()) == []


def test_plan_refuses_noncanonical_manifest_backup_mapping(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    migration_id = "0" * 16
    backup_root = prefix / "backups" / "legacy-migration" / migration_id
    source = prefix / "bin" / "rt-say"
    manifest = {
        "schema": migrate.SCHEMA,
        "state": "rolled-back",
        "legacy_head": "0" * 40,
        "migration_id": migration_id,
        "home": str(home),
        "prefix": str(prefix),
        "link_dir": str(link_dir),
        "backup_root": str(backup_root),
        "items": [
            {
                "path": str(source),
                "relative_to_home": source.relative_to(home).as_posix(),
                "backup": str(backup_root / "payload" / "wrong-name"),
                "kind": "file",
                "reason": "malicious manifest item",
                "mode": 0o755,
                "sha256": "0" * 64,
                "target": None,
                "desired_mode": None,
            }
        ],
        "preserved": [],
    }
    _write(prefix / migrate.MANIFEST_NAME, json.dumps(manifest), 0o600)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "not canonical for its source" in result["error"]
    assert source.is_file()


def test_plan_refuses_noncanonical_manifest_source_path(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    home, prefix, link_dir = legacy_install
    plan = migrate._plan(home, prefix, link_dir)
    manifest = migrate._manifest_payload(
        plan,
        "rolled-back",
        home=home,
        prefix=prefix,
        link_dir=link_dir,
    )
    item = manifest["items"][0]
    source = Path(item["path"])
    item["path"] = str(source.parent / ".." / "bin" / source.name)
    _write(prefix / migrate.MANIFEST_NAME, json.dumps(manifest), 0o600)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "path mismatch" in result["error"]
    assert source.is_file()


@pytest.mark.parametrize("tamper", ["target", "digest", "non-link-path"])
def test_plan_refuses_tampered_manifest_symlink_item(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    tamper: str,
) -> None:
    home, prefix, link_dir = legacy_install
    plan = migrate._plan(home, prefix, link_dir)
    manifest = migrate._manifest_payload(
        plan,
        "rolled-back",
        home=home,
        prefix=prefix,
        link_dir=link_dir,
    )
    if tamper == "non-link-path":
        item = next(value for value in manifest["items"] if value["kind"] == "file")
        path = Path(item["path"])
        item.update(
            {
                "kind": "symlink",
                "mode": None,
                "target": path.name,
                "sha256": migrate._sha256_bytes(path.name.encode("utf-8")),
            }
        )
    else:
        item = next(value for value in manifest["items"] if value["kind"] == "symlink")
        if tamper == "target":
            item["target"] = "../../foreign-command"
            item["sha256"] = migrate._sha256_bytes(item["target"].encode("utf-8"))
        else:
            item["sha256"] = "0" * 64
    _write(prefix / migrate.MANIFEST_NAME, json.dumps(manifest), 0o600)

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    assert "symlink" in result["error"]


@pytest.mark.parametrize("manifest_kind", ["broken-symlink", "unsafe-mode"])
def test_plan_refuses_unsafe_migration_manifest_leaf(
    legacy_install: tuple[Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
    manifest_kind: str,
) -> None:
    home, prefix, link_dir = legacy_install
    manifest_path = prefix / migrate.MANIFEST_NAME
    if manifest_kind == "broken-symlink":
        manifest_path.symlink_to(home / "missing-manifest-target")
    else:
        _write(
            manifest_path,
            json.dumps({"schema": migrate.SCHEMA}),
            0o666,
        )

    code, result = _run(capsys, home, prefix, link_dir, "plan")

    assert code == 2
    if manifest_kind == "broken-symlink":
        assert "expected file, found symlink" in result["error"]
        assert manifest_path.is_symlink()
    else:
        assert "unsafe mode" in result["error"]
        assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o666


def test_absent_and_managed_prefix_are_noop(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    prefix = home / ".roundtable"
    link_dir = home / ".local" / "bin"

    code, missing = _run(capsys, home, prefix, link_dir, "apply")
    assert code == 0
    assert missing["state"] == "not-found"
    assert not prefix.exists()

    prefix.mkdir()
    (prefix / "install-manifest.json").write_text(
        json.dumps({"schema": migrate.MANAGED_INSTALL_SCHEMA, "prefix": str(prefix)})
    )
    code, managed = _run(capsys, home, prefix, link_dir, "apply")
    assert code == 0
    assert managed["state"] == "managed-install"
    assert not (prefix / "backups").exists()
