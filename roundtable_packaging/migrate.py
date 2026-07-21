"""Conservative migration of pre-manifest Roundtable installations.

The managed installer intentionally refuses to overwrite an unexplained
``~/.roundtable/bin`` tree or harness configuration.  This module prepares one
very specific predecessor layout for that installer:

* program files must be clean, tracked files in a Git-backed Roundtable tree;
* the canonical skill may contain only its clean, tracked ``SKILL.md``;
* command links must resolve to the matching legacy command under the prefix;
* Codex LaunchAgents must match the old Roundtable-owned structure exactly.
* only the known ``0755`` runtime-root modes are tightened to ``0700``.

Project registries, project maildirs, runtime state, documentation, and every
other path are outside the mutation set.  ``plan`` is read-only.  ``apply``
backs up every selected leaf before unlinking it, records directory modes
before changing metadata, and ``rollback`` restores the recorded bytes, modes,
and link targets.  This tool never loads, unloads, or restarts a service.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


SCHEMA = "roundtable.legacy-migration.v1"
MANAGED_INSTALL_SCHEMA = "roundtable.install.v1"
MANIFEST_NAME = "legacy-migration.json"

# These are the only predecessor command leaves that the migration can claim.
# A newer package may add commands without making the legacy recognizer broader.
LEGACY_BIN_NAMES = frozenset(
    {
        "_rtcodex.py",
        "_rtlauncher.py",
        "_rtlib.py",
        "_rtruntime.py",
        "roundtable",
        "roundtable-init",
        "rt-ack",
        "rt-claude",
        "rt-codex",
        "rt-codex-daemon",
        "rt-codex-wake",
        "rt-doctor",
        "rt-hermes",
        "rt-inbox",
        "rt-projects",
        "rt-refresh",
        "rt-resolve",
        "rt-say",
        "rt-startup-advisory",
        "rt-stop-gate",
        "rt-wait-inbox",
    }
)
LEGACY_TOOL_NAMES = frozenset(name for name in LEGACY_BIN_NAMES if not name.startswith("_"))
CODEX_LABELS = (
    "com.roundtable.codex-app-server",
    "com.roundtable.codex-wake",
)
_PLIST_COMMON_KEYS = frozenset(
    {
        "Label",
        "ProgramArguments",
        "RunAtLoad",
        "KeepAlive",
        "ThrottleInterval",
        "ProcessType",
        "WorkingDirectory",
        "EnvironmentVariables",
        "StandardOutPath",
        "StandardErrorPath",
    }
)
_PLIST_ENV_KEYS = frozenset(
    {
        "HOME",
        "PATH",
        "CODEX_HOME",
        "RT_RUNTIME_DIR",
        "RT_CODEX_RUNTIME_DIR",
        "RT_CODEX_BIN",
        "ROUNDTABLE_INSTALL_PREFIX",
        "RT_PROJECTS_FILE",
    }
)
_GIT_REPOSITORY_ENV_KEYS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CONFIG",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_EXEC_PATH",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
    }
)


class MigrationError(RuntimeError):
    """A fail-closed recognition, ownership, or rollback error."""


@dataclass(frozen=True)
class Candidate:
    path: Path
    kind: str
    reason: str
    mode: int | None
    digest: str | None = None
    target: str | None = None
    desired_mode: int | None = None


@dataclass
class OperationAudit:
    writes: bool = False
    launchctl_invoked: bool = False


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _kind(info: os.stat_result) -> str:
    if stat.S_ISREG(info.st_mode):
        return "file"
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    return "special"


def _inspect_owned(path: Path, expected: str | None = None) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MigrationError(f"cannot inspect {path}: {error}") from error
    actual = _kind(info)
    if info.st_uid != os.getuid():
        raise MigrationError(
            f"refusing path owned by uid {info.st_uid}, expected {os.getuid()}: {path}"
        )
    if expected is not None and actual != expected:
        raise MigrationError(f"expected {expected}, found {actual}: {path}")
    return info


def _validate_chain(home: Path, path: Path) -> None:
    anchor = _inspect_owned(home, "directory")
    if anchor is None:
        raise MigrationError(f"migration path anchor does not exist: {home}")
    try:
        relative = path.relative_to(home)
    except ValueError as error:
        raise MigrationError(f"migration path escapes home {home}: {path}") from error
    current = home
    for part in relative.parts:
        current = current / part
        if not _lexists(current):
            break
        info = _inspect_owned(current)
        assert info is not None
        if stat.S_ISLNK(info.st_mode):
            raise MigrationError(f"refusing symlink in migration path: {current}")


def _run_git(prefix: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if (
            key in _GIT_REPOSITORY_ENV_KEYS
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        ):
            environment.pop(key, None)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        process = subprocess.run(
            ["git", "-C", str(prefix), *arguments],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise MigrationError(f"cannot inspect legacy Git tree: {error}") from error
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or f"exit {process.returncode}"
        raise MigrationError(f"cannot inspect legacy Git tree: {detail}")
    return process


def _git_head(prefix: Path) -> str:
    git_dir = prefix / ".git"
    _inspect_owned(git_dir, "directory")
    top_level = _run_git(prefix, "rev-parse", "--show-toplevel").stdout.strip()
    if top_level != str(prefix):
        raise MigrationError(
            f"legacy Git worktree top level is {top_level!r}, expected {str(prefix)!r}"
        )
    value = _run_git(prefix, "rev-parse", "--verify", "HEAD").stdout.strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise MigrationError("legacy Git tree has an invalid HEAD")
    return value


def _require_clean_tracked(prefix: Path, relative: PurePosixPath, path: Path) -> None:
    name = relative.as_posix()
    tracked = _run_git(prefix, "ls-files", "--error-unmatch", "--", name, check=False)
    if tracked.returncode != 0:
        raise MigrationError(f"refusing untracked legacy program path: {path}")
    expected = _run_git(prefix, "show", f"HEAD:{name}", check=False)
    if expected.returncode != 0:
        raise MigrationError(f"cannot read legacy Git object for {path}")
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MigrationError(f"cannot read legacy program path {path}: {error}") from error
    # ``subprocess(text=True)`` is intentional: predecessor tools were text
    # files, and this also rejects unexpected binary leaves.
    if current != expected.stdout:
        raise MigrationError(f"refusing modified legacy program path: {path}")


def _program_candidates(prefix: Path) -> tuple[str, list[Candidate]]:
    head = _git_head(prefix)
    bin_dir = prefix / "bin"
    _validate_chain(prefix, bin_dir)
    _inspect_owned(bin_dir, "directory")
    candidates: list[Candidate] = []
    recognized = 0
    for path in sorted(bin_dir.iterdir(), key=lambda value: value.name):
        info = _inspect_owned(path)
        assert info is not None
        if path.name == "__pycache__" and stat.S_ISDIR(info.st_mode):
            # Bytecode is not a collision with the managed wrappers and is not
            # claimed or removed by migration.
            continue
        if path.name not in LEGACY_BIN_NAMES:
            raise MigrationError(f"refusing unknown path in legacy bin directory: {path}")
        if not stat.S_ISREG(info.st_mode):
            raise MigrationError(f"expected clean tracked file in legacy bin: {path}")
        relative = PurePosixPath("bin") / path.name
        _require_clean_tracked(prefix, relative, path)
        candidates.append(
            Candidate(
                path=path,
                kind="file",
                reason="clean tracked legacy program",
                mode=stat.S_IMODE(info.st_mode),
                digest=_sha256_path(path),
            )
        )
        recognized += 1
    required = {"_rtlib.py", "roundtable-init", "rt-say", "rt-inbox", "rt-ack"}
    present = {candidate.path.name for candidate in candidates}
    if not required <= present or recognized < len(required):
        missing = ", ".join(sorted(required - present))
        raise MigrationError(
            "legacy bin tree does not match the supported Roundtable layout"
            + (f"; missing {missing}" if missing else "")
        )

    try:
        library_text = (bin_dir / "_rtlib.py").read_text(encoding="utf-8")
        sender_text = (bin_dir / "rt-say").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise MigrationError(f"cannot inspect legacy Roundtable markers: {error}") from error
    if (
        'PROJECTS_SCHEMA = "roundtable.projects.v1"' not in library_text
        or "from _rtlib import find_project_root, load_agents_doc" not in sender_text
        or "maildir" not in sender_text
    ):
        raise MigrationError(
            "legacy Git tree does not contain the supported Roundtable identity markers"
        )

    skill_root = prefix / "skills" / "shared" / "roundtable"
    _validate_chain(prefix, skill_root)
    if _lexists(skill_root):
        _inspect_owned(skill_root, "directory")
        children = sorted(skill_root.iterdir(), key=lambda value: value.name)
        if [child.name for child in children] != ["SKILL.md"]:
            raise MigrationError(
                f"refusing customized legacy skill directory: {skill_root}"
            )
        skill = children[0]
        info = _inspect_owned(skill, "file")
        assert info is not None
        _require_clean_tracked(
            prefix,
            PurePosixPath("skills/shared/roundtable/SKILL.md"),
            skill,
        )
        candidates.append(
            Candidate(
                path=skill,
                kind="file",
                reason="clean tracked legacy canonical skill",
                mode=stat.S_IMODE(info.st_mode),
                digest=_sha256_path(skill),
            )
        )
    return head, candidates


def _expected_runtime(prefix: Path) -> Path:
    return prefix / ".runtime"


def _expected_codex_socket(home: Path) -> Path:
    return home / ".codex" / "app-server-control" / "app-server-control.sock"


def _path_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _recognize_plist(path: Path, label: str, home: Path, prefix: Path) -> Candidate:
    info = _inspect_owned(path, "file")
    assert info is not None
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise MigrationError(f"refusing unreadable legacy LaunchAgent: {path}") from error
    if not isinstance(value, dict) or set(value) != set(_PLIST_COMMON_KEYS):
        raise MigrationError(f"refusing customized legacy LaunchAgent: {path}")
    if (
        value.get("Label") != label
        or value.get("RunAtLoad") is not True
        or value.get("ThrottleInterval") != 5
        or value.get("ProcessType") != "Background"
        or value.get("WorkingDirectory") != str(home)
    ):
        raise MigrationError(f"refusing customized legacy LaunchAgent: {path}")
    expected_keep_alive: object = (
        True if label == CODEX_LABELS[0] else {"SuccessfulExit": False}
    )
    if value.get("KeepAlive") != expected_keep_alive:
        raise MigrationError(f"refusing customized legacy LaunchAgent: {path}")

    runtime = _expected_runtime(prefix)
    stem = "codex-app-server" if label == CODEX_LABELS[0] else "rt-codex-wake"
    if (
        value.get("StandardOutPath") != str(runtime / f"{stem}.stdout.log")
        or value.get("StandardErrorPath") != str(runtime / f"{stem}.stderr.log")
    ):
        raise MigrationError(f"refusing customized legacy LaunchAgent: {path}")

    environment = value.get("EnvironmentVariables")
    if not isinstance(environment, dict) or not set(environment) <= _PLIST_ENV_KEYS:
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    if environment.get("HOME") != str(home):
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    codex_home = environment.get("CODEX_HOME")
    if codex_home != str(home / ".codex"):
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    expected_path = ":".join(
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
    if environment.get("PATH") != expected_path:
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    for key in ("RT_RUNTIME_DIR", "RT_CODEX_RUNTIME_DIR"):
        if key in environment and environment[key] != str(runtime):
            raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    if "ROUNDTABLE_INSTALL_PREFIX" in environment and environment[
        "ROUNDTABLE_INSTALL_PREFIX"
    ] != str(prefix):
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")
    projects_file = environment.get("RT_PROJECTS_FILE")
    if projects_file is not None and projects_file != str(prefix / "projects.yaml"):
        raise MigrationError(f"refusing customized legacy LaunchAgent environment: {path}")

    arguments = value.get("ProgramArguments")
    socket = _expected_codex_socket(home)
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise MigrationError(f"refusing customized legacy LaunchAgent arguments: {path}")
    if label == CODEX_LABELS[0]:
        if len(arguments) != 4 or arguments[1:] != [
            "app-server",
            "--listen",
            f"unix://{socket}",
        ]:
            raise MigrationError(f"refusing customized legacy LaunchAgent arguments: {path}")
        executable = Path(arguments[0])
        allowed_roots = (
            home / ".npm-global",
            home / ".codex" / "packages" / "standalone",
            home / ".local" / "bin",
        )
        if executable != _absolute(executable) or not any(
            _path_under(executable, root) for root in allowed_roots
        ):
            raise MigrationError(f"refusing unknown Codex executable in {path}")
        configured = environment.get("RT_CODEX_BIN")
        if configured is not None and configured != arguments[0]:
            raise MigrationError(f"refusing inconsistent Codex executable in {path}")
    else:
        expected = [
            str(prefix / "bin" / "rt-codex-wake"),
            "--socket",
            str(socket),
            "run",
        ]
        if arguments not in (expected, [*expected, "--auto-discover"]):
            raise MigrationError(f"refusing customized legacy LaunchAgent arguments: {path}")

    return Candidate(
        path=path,
        kind="file",
        reason=f"recognized legacy LaunchAgent {label}",
        mode=stat.S_IMODE(info.st_mode),
        digest=_sha256_path(path),
    )


def _launch_agent_candidates(home: Path, prefix: Path) -> list[Candidate]:
    root = Path(
        os.environ.get("RT_LAUNCH_AGENTS_DIR", home / "Library" / "LaunchAgents")
    ).expanduser()
    root = _absolute(root)
    _validate_chain(home, root)
    candidates = []
    for label in CODEX_LABELS:
        path = root / f"{label}.plist"
        if _lexists(path):
            candidates.append(_recognize_plist(path, label, home, prefix))
    return candidates


def _link_candidates(home: Path, prefix: Path, link_dir: Path) -> list[Candidate]:
    _validate_chain(home, link_dir)
    candidates = []
    if not link_dir.exists():
        return candidates
    _inspect_owned(link_dir, "directory")
    for name in sorted(LEGACY_TOOL_NAMES):
        path = link_dir / name
        if not _lexists(path):
            continue
        info = _inspect_owned(path)
        assert info is not None
        if not stat.S_ISLNK(info.st_mode):
            raise MigrationError(f"refusing non-symlink at legacy command link: {path}")
        target = os.readlink(path)
        try:
            resolved = (path.parent / target).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise MigrationError(
                f"refusing unresolvable legacy command link: {path} -> {target}"
            ) from error
        expected = prefix / "bin" / name
        if resolved != expected:
            raise MigrationError(f"refusing foreign legacy command link: {path} -> {target}")
        # The managed installer writes an absolute target.  Existing absolute
        # links already match and need no migration; relative predecessor links
        # are backed up and removed so installation can recreate them.
        if target == str(expected):
            continue
        candidates.append(
            Candidate(
                path=path,
                kind="symlink",
                reason="recognized relative legacy command link",
                mode=None,
                target=target,
                digest=_sha256_bytes(target.encode("utf-8")),
            )
        )
    return candidates


def _runtime_mode_candidates(prefix: Path) -> list[Candidate]:
    """Recognize the two legacy runtime directories that were created 0755.

    Runtime contents remain out of scope.  Only these exact owned directory
    leaves may have their mode tightened, and only from the known predecessor
    mode to the current private-directory mode.
    """

    candidates = []
    for path in (prefix / ".runtime", prefix / ".runtime" / "projects"):
        if not _lexists(path):
            continue
        info = _inspect_owned(path, "directory")
        assert info is not None
        mode = stat.S_IMODE(info.st_mode)
        if mode == 0o700:
            continue
        if mode != 0o755:
            raise MigrationError(
                f"refusing unsupported legacy runtime directory mode {mode:04o}: {path}"
            )
        candidates.append(
            Candidate(
                path=path,
                kind="directory-mode",
                reason="tighten recognized legacy runtime directory without changing contents",
                mode=mode,
                desired_mode=0o700,
            )
        )
    return candidates


def _manifest_path(prefix: Path) -> Path:
    return prefix / MANIFEST_NAME


def _load_json(path: Path) -> dict | None:
    if not _lexists(path):
        return None
    info = _inspect_owned(path, "file")
    assert info is not None
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise MigrationError(f"refusing migration manifest with unsafe mode: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"cannot read migration manifest {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise MigrationError(f"refusing unknown migration manifest at {path}")
    return value


def _managed_install_present(prefix: Path) -> bool:
    path = prefix / "install-manifest.json"
    if not _lexists(path):
        return False
    _inspect_owned(path, "file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"cannot read managed install manifest {path}: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != MANAGED_INSTALL_SCHEMA:
        raise MigrationError(f"refusing unknown install manifest at {path}")
    if value.get("prefix") != str(prefix):
        raise MigrationError(f"managed install manifest prefix mismatch at {path}")
    return True


def _candidate_record(candidate: Candidate, home: Path, backup_root: Path) -> dict:
    relative = candidate.path.relative_to(home)
    backup = (
        None
        if candidate.kind == "directory-mode"
        else str(backup_root / "payload" / relative)
    )
    return {
        "path": str(candidate.path),
        "relative_to_home": relative.as_posix(),
        "backup": backup,
        "kind": candidate.kind,
        "reason": candidate.reason,
        "mode": candidate.mode,
        "sha256": candidate.digest,
        "target": candidate.target,
        "desired_mode": candidate.desired_mode,
    }


def _validated_backup_root(prefix: Path, migration_id: object, backup_value: object) -> Path:
    if (
        not isinstance(migration_id, str)
        or len(migration_id) != 16
        or any(character not in "0123456789abcdef" for character in migration_id)
        or not isinstance(backup_value, str)
    ):
        raise MigrationError("migration manifest has invalid backup metadata")
    backup_root = _absolute(backup_value)
    expected = prefix / "backups" / "legacy-migration" / migration_id
    if backup_value != str(backup_root) or backup_root != expected:
        raise MigrationError("migration manifest backup root escapes its migration id")
    _validate_chain(prefix, backup_root)
    return backup_root


def _plan(home: Path, prefix: Path, link_dir: Path) -> dict:
    _validate_chain(home, prefix)
    _validate_chain(home, link_dir)
    if not prefix.exists():
        return {
            "schema": SCHEMA,
            "state": "not-found",
            "manifest": str(_manifest_path(prefix)),
            "actions": [],
            "preserved": [],
            "writes": False,
            "launchctl_invoked": False,
        }
    if _managed_install_present(prefix):
        return {
            "schema": SCHEMA,
            "state": "managed-install",
            "manifest": str(_manifest_path(prefix)),
            "actions": [],
            "preserved": [
                str(prefix / "projects.yaml"),
                str(prefix / "projects.yaml.lock"),
                str(prefix / ".runtime"),
                "all project-local .roundtable mailboxes",
            ],
            "writes": False,
            "launchctl_invoked": False,
        }

    existing = _load_json(_manifest_path(prefix))
    if existing is not None:
        if (
            existing.get("home") != str(home)
            or existing.get("prefix") != str(prefix)
            or existing.get("link_dir") != str(link_dir)
        ):
            raise MigrationError(
                "migration manifest home, prefix, or link directory does not match this command"
            )
        migration_id = existing.get("migration_id")
        backup_root = _validated_backup_root(
            prefix,
            migration_id,
            existing.get("backup_root"),
        )
        items = existing.get("items")
        if not isinstance(items, list):
            raise MigrationError("migration manifest has invalid items")
        for item in items:
            _verify_manifest_item(item, home, prefix, link_dir, backup_root)
        return {
            "schema": SCHEMA,
            "state": existing.get("state"),
            "legacy_head": existing.get("legacy_head"),
            "migration_id": migration_id,
            "manifest": str(_manifest_path(prefix)),
            "backup_root": str(backup_root),
            "actions": items,
            "preserved": existing.get("preserved") or [],
            "writes": False,
            "launchctl_invoked": False,
        }

    head, program = _program_candidates(prefix)
    candidates = [
        *program,
        *_link_candidates(home, prefix, link_dir),
        *_launch_agent_candidates(home, prefix),
        *_runtime_mode_candidates(prefix),
    ]
    fingerprint = hashlib.sha256()
    fingerprint.update(head.encode("ascii"))
    for candidate in sorted(candidates, key=lambda item: str(item.path)):
        fingerprint.update(str(candidate.path).encode("utf-8"))
        fingerprint.update((candidate.digest or "").encode("ascii"))
        fingerprint.update(str(candidate.mode).encode("ascii"))
        fingerprint.update(str(candidate.desired_mode).encode("ascii"))
    migration_id = fingerprint.hexdigest()[:16]
    backup_root = prefix / "backups" / "legacy-migration" / migration_id
    preserved = [
        str(prefix / "projects.yaml"),
        str(prefix / "projects.yaml.lock"),
        str(prefix / ".runtime"),
        "all project-local .roundtable mailboxes",
        "all unlisted legacy files and directories",
    ]
    return {
        "schema": SCHEMA,
        "state": "ready",
        "legacy_head": head,
        "migration_id": migration_id,
        "manifest": str(_manifest_path(prefix)),
        "backup_root": str(backup_root),
        "actions": [
            _candidate_record(candidate, home, backup_root) for candidate in candidates
        ],
        "preserved": preserved,
        "writes": False,
        "launchctl_invoked": False,
    }


def _launchctl_loaded(label: str, audit: OperationAudit) -> tuple[bool, str]:
    executable = os.environ.get("RT_LAUNCHCTL", "/bin/launchctl")
    try:
        process = subprocess.run(
            [executable, "print", f"gui/{os.getuid()}/{label}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        audit.launchctl_invoked = True
    except OSError as error:
        raise MigrationError(f"cannot verify whether {label} is loaded: {error}") from error
    if process.returncode == 0:
        return True, process.stdout.strip()
    detail = (process.stderr.strip() or process.stdout.strip()).lower()
    if process.returncode == 113 and "could not find service" in detail:
        return False, detail
    raise MigrationError(
        f"cannot prove that {label} is stopped: "
        + (process.stderr.strip() or process.stdout.strip() or f"launchctl exit {process.returncode}")
    )


def _require_services_stopped(audit: OperationAudit) -> None:
    # A launchd job can remain loaded after its plist is deleted.  Always check
    # both known labels instead of inferring the live service set from files
    # selected by the migration plan.
    if os.environ.get("CODEX_THREAD_ID"):
        raise MigrationError(
            "refusing legacy migration from inside Codex; run apply outside Codex in a normal terminal"
        )
    loaded = []
    for label in CODEX_LABELS:
        active, _detail = _launchctl_loaded(label, audit)
        if active:
            loaded.append(label)
    if loaded:
        raise MigrationError(
            "refusing to remove legacy LaunchAgent files while services are loaded: "
            + ", ".join(loaded)
            + "; stop them in a coordinated cutover first"
        )


def _ensure_private_dir(path: Path, audit: OperationAudit) -> None:
    if _lexists(path):
        info = _inspect_owned(path, "directory")
        assert info is not None
    else:
        path.mkdir(mode=0o700)
        audit.writes = True
        info = path.lstat()
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
        audit.writes = True


def _ensure_owned_directory_chain(
    anchor: Path, path: Path, audit: OperationAudit
) -> None:
    """Create directories below an owned anchor without following symlinks."""

    _inspect_owned(anchor, "directory")
    try:
        relative = path.relative_to(anchor)
    except ValueError as error:
        raise MigrationError(f"private directory escapes backup root: {path}") from error
    current = anchor
    for part in relative.parts:
        current = current / part
        if _lexists(current):
            info = _inspect_owned(current, "directory")
            assert info is not None
        else:
            current.mkdir(mode=0o700)
            audit.writes = True
            info = current.lstat()
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.chmod(current, 0o700)
            audit.writes = True


def _atomic_write(
    path: Path, payload: bytes, mode: int, audit: OperationAudit
) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        audit.writes = True
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        audit.writes = True
        os.chmod(path, mode)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _atomic_symlink(path: Path, target: str, audit: OperationAudit) -> None:
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        audit.writes = True
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    temporary.unlink(missing_ok=True)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, path)
        audit.writes = True
    finally:
        temporary.unlink(missing_ok=True)


def _backup_item(item: dict, audit: OperationAudit) -> None:
    if item["kind"] == "directory-mode":
        # The applying manifest records the original and desired modes before
        # chmod.  Directory contents are durable state and are never copied,
        # moved, enumerated, or removed by migration.
        return
    source = Path(item["path"])
    backup = Path(item["backup"])
    backup_root = Path(item["backup"])
    while backup_root.name != "payload" and backup_root != backup_root.parent:
        backup_root = backup_root.parent
    if backup_root.name != "payload":
        raise MigrationError(f"invalid migration backup path: {backup}")
    _ensure_owned_directory_chain(backup_root.parent, backup.parent, audit)
    if _lexists(backup):
        _verify_backup(item)
        return
    if item["kind"] == "file":
        _inspect_owned(source, "file")
        if _sha256_path(source) != item["sha256"]:
            raise MigrationError(f"legacy file changed after plan: {source}")
        _atomic_write(backup, source.read_bytes(), 0o600, audit)
    elif item["kind"] == "symlink":
        _inspect_owned(source, "symlink")
        if os.readlink(source) != item["target"]:
            raise MigrationError(f"legacy link changed after plan: {source}")
        _atomic_symlink(backup, item["target"], audit)
    else:
        raise MigrationError(f"unsupported migration item kind: {item['kind']}")
    # Hash/read and link inspection are necessarily separate path operations.
    # Rechecking the completed backup prevents a concurrent source replacement
    # from being followed by deletion with an unusable rollback payload.
    _verify_backup(item)


def _verify_backup(item: dict) -> None:
    if item["kind"] == "directory-mode":
        return
    backup = Path(item["backup"])
    if item["kind"] == "file":
        _inspect_owned(backup, "file")
        if _sha256_path(backup) != item["sha256"]:
            raise MigrationError(f"migration backup was modified: {backup}")
    elif item["kind"] == "symlink":
        _inspect_owned(backup, "symlink")
        if os.readlink(backup) != item["target"]:
            raise MigrationError(f"migration backup link was modified: {backup}")
    else:
        raise MigrationError(f"unsupported migration item kind: {item['kind']}")


def _source_matches(item: dict) -> bool:
    path = Path(item["path"])
    if not _lexists(path):
        return False
    if item["kind"] == "file":
        info = _inspect_owned(path)
        return bool(info and stat.S_ISREG(info.st_mode) and _sha256_path(path) == item["sha256"])
    if item["kind"] == "symlink":
        info = _inspect_owned(path)
        return bool(info and stat.S_ISLNK(info.st_mode) and os.readlink(path) == item["target"])
    if item["kind"] == "directory-mode":
        info = _inspect_owned(path)
        return bool(
            info
            and stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == item["mode"]
        )
    return False


def _applied_matches(item: dict) -> bool:
    path = Path(item["path"])
    if item["kind"] == "directory-mode":
        if not _lexists(path):
            return False
        info = _inspect_owned(path)
        return bool(
            info
            and stat.S_ISDIR(info.st_mode)
            and stat.S_IMODE(info.st_mode) == item["desired_mode"]
        )
    return not _lexists(path)


def _remove_item(item: dict, audit: OperationAudit) -> None:
    path = Path(item["path"])
    if item["kind"] == "directory-mode":
        if not _source_matches(item):
            raise MigrationError(f"legacy runtime directory changed after backup: {path}")
        os.chmod(path, int(item["desired_mode"]))
        audit.writes = True
        return
    if not _lexists(path):
        return
    if not _source_matches(item):
        raise MigrationError(f"legacy path changed after backup: {path}")
    path.unlink()
    audit.writes = True
    skill_root = path.parent
    if path.name == "SKILL.md" and skill_root.name == "roundtable":
        try:
            skill_root.rmdir()
        except OSError as error:
            raise MigrationError(f"cannot remove empty legacy skill directory {skill_root}: {error}") from error


def _manifest_payload(
    plan: dict, state: str, *, home: Path, prefix: Path, link_dir: Path
) -> dict:
    return {
        "schema": SCHEMA,
        "state": state,
        "legacy_head": plan["legacy_head"],
        "migration_id": plan["migration_id"],
        "home": str(home),
        "prefix": str(prefix),
        "link_dir": str(link_dir),
        "backup_root": plan["backup_root"],
        "items": plan["actions"],
        "preserved": plan["preserved"],
    }


@contextmanager
def _mutation_lock(prefix: Path, audit: OperationAudit) -> Iterator[None]:
    _inspect_owned(prefix, "directory")
    backups = prefix / "backups"
    if _lexists(backups):
        _inspect_owned(backups, "directory")
    else:
        backups.mkdir(mode=0o700)
        audit.writes = True
    root = backups / "legacy-migration"
    if _lexists(root):
        root_info = _inspect_owned(root, "directory")
        assert root_info is not None
    else:
        root.mkdir(mode=0o700)
        audit.writes = True
        root_info = root.lstat()
    if stat.S_IMODE(root_info.st_mode) != 0o700:
        os.chmod(root, 0o700)
        audit.writes = True
    path = root / ".lock"
    lock_existed = _lexists(path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    info = os.fstat(descriptor)
    if not lock_existed:
        audit.writes = True
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise MigrationError(f"refusing unsafe migration lock: {path}")
    if stat.S_IMODE(info.st_mode) != 0o600:
        os.fchmod(descriptor, 0o600)
        audit.writes = True
    with os.fdopen(descriptor, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _apply(
    home: Path, prefix: Path, link_dir: Path, audit: OperationAudit
) -> dict:
    initial = _plan(home, prefix, link_dir)
    if initial["state"] == "managed-install":
        return {**initial, "command": "apply", "message": "already managed"}
    if initial["state"] == "not-found":
        return {**initial, "command": "apply", "message": "no legacy install found"}
    if initial["state"] in {"ready", "rolled-back"}:
        # Fail before even creating the migration lock or backup directories.
        _require_services_stopped(audit)
    with _mutation_lock(prefix, audit):
        plan = _plan(home, prefix, link_dir)
        if plan["state"] == "applied":
            for item in plan["actions"]:
                _verify_backup(item)
                if not _applied_matches(item):
                    raise MigrationError(
                        f"migration is recorded as applied but path state differs: {item['path']}"
                    )
            return {
                **plan,
                "command": "apply",
                "writes": audit.writes,
                "launchctl_invoked": audit.launchctl_invoked,
                "message": "legacy migration already applied",
            }
        if plan["state"] not in {"ready", "rolled-back"}:
            raise MigrationError(f"cannot apply migration in state {plan['state']!r}")
        _require_services_stopped(audit)

        manifest_path = _manifest_path(prefix)
        backup_root = Path(plan["backup_root"])
        _ensure_private_dir(backup_root, audit)
        for item in plan["actions"]:
            if not _source_matches(item):
                raise MigrationError(f"legacy path changed before apply: {item['path']}")
        for item in plan["actions"]:
            _backup_item(item, audit)
        applying = _manifest_payload(
            plan, "applying", home=home, prefix=prefix, link_dir=link_dir
        )
        _atomic_write(manifest_path, _json_bytes(applying), 0o600, audit)
        try:
            for item in plan["actions"]:
                _remove_item(item, audit)
            applied = {**applying, "state": "applied"}
            _atomic_write(manifest_path, _json_bytes(applied), 0o600, audit)
        except Exception:
            # Restore everything removed so far.  Leave a durable manifest and
            # backups even when recovery succeeds, making the outcome auditable.
            rollback_errors = []
            for item in reversed(plan["actions"]):
                try:
                    _restore_item(item, audit)
                except Exception as error:  # pragma: no cover - defensive crash path
                    rollback_errors.append(str(error))
            state = "rollback-failed" if rollback_errors else "rolled-back"
            _atomic_write(
                manifest_path,
                _json_bytes({**applying, "state": state}),
                0o600,
                audit,
            )
            if rollback_errors:
                raise MigrationError(
                    "legacy migration failed and rollback was incomplete: "
                    + " | ".join(rollback_errors)
                )
            raise
        return {
            **plan,
            "command": "apply",
            "state": "applied",
            "writes": audit.writes,
            "launchctl_invoked": audit.launchctl_invoked,
            "message": "legacy program paths backed up and removed; run the managed installer next",
        }


def _restore_item(item: dict, audit: OperationAudit) -> None:
    _verify_backup(item)
    path = Path(item["path"])
    if item["kind"] == "directory-mode":
        info = _inspect_owned(path, "directory")
        if info is None:
            raise MigrationError(f"refusing rollback of missing runtime directory: {path}")
        current = stat.S_IMODE(info.st_mode)
        if current not in {item["mode"], item["desired_mode"]}:
            raise MigrationError(f"refusing rollback of changed directory mode: {path}")
        if current != item["mode"]:
            os.chmod(path, int(item["mode"]))
            audit.writes = True
        return
    if _lexists(path):
        if _source_matches(item):
            return
        raise MigrationError(f"refusing rollback over a different path: {path}")
    if item["kind"] == "file":
        _atomic_write(
            path,
            Path(item["backup"]).read_bytes(),
            int(item["mode"]),
            audit,
        )
    elif item["kind"] == "symlink":
        _atomic_symlink(path, item["target"], audit)
    else:
        raise MigrationError(f"unsupported migration item kind: {item['kind']}")


def _rollback(home: Path, prefix: Path, audit: OperationAudit) -> dict:
    _validate_chain(home, prefix)
    manifest_path = _manifest_path(prefix)
    if _load_json(manifest_path) is None:
        raise MigrationError(f"no legacy migration manifest at {manifest_path}")
    with _mutation_lock(prefix, audit):
        manifest = _load_json(manifest_path)
        assert manifest is not None
        if manifest.get("home") != str(home) or manifest.get("prefix") != str(prefix):
            raise MigrationError("migration manifest home or prefix does not match this command")
        state = manifest.get("state")
        if state == "rollback-failed":
            raise MigrationError("previous rollback was incomplete; manual recovery is required")
        if state not in {"applied", "applying", "rolled-back"}:
            raise MigrationError(f"cannot rollback migration in state {state!r}")
        items = manifest.get("items")
        if not isinstance(items, list):
            raise MigrationError("migration manifest has invalid items")
        migration_id = manifest.get("migration_id")
        backup_root = _validated_backup_root(
            prefix,
            migration_id,
            manifest.get("backup_root"),
        )
        link_value = manifest.get("link_dir")
        if not isinstance(link_value, str):
            raise MigrationError("migration manifest has invalid link directory")
        recorded_link_dir = _absolute(link_value)
        if link_value != str(recorded_link_dir):
            raise MigrationError("migration manifest has a non-canonical link directory")
        _validate_chain(home, recorded_link_dir)
        for item in items:
            _verify_manifest_item(
                item,
                home,
                prefix,
                recorded_link_dir,
                backup_root,
            )
            _verify_backup(item)
            path = Path(item["path"])
            if (
                _lexists(path)
                and not _source_matches(item)
                and not _applied_matches(item)
            ):
                raise MigrationError(f"refusing rollback over a different path: {path}")
        writes = any(not _source_matches(item) for item in items)
        if state == "rolled-back" and not writes:
            return {
                "schema": SCHEMA,
                "command": "rollback",
                "state": "rolled-back",
                "migration_id": manifest.get("migration_id"),
                "manifest": str(manifest_path),
                "actions": items,
                "preserved": manifest.get("preserved") or [],
                "writes": audit.writes,
                "launchctl_invoked": audit.launchctl_invoked,
                "message": "legacy migration is already rolled back",
            }
        _require_services_stopped(audit)
        for item in reversed(items):
            _restore_item(item, audit)
        rolled_back = {**manifest, "state": "rolled-back"}
        _atomic_write(manifest_path, _json_bytes(rolled_back), 0o600, audit)
        return {
            "schema": SCHEMA,
            "command": "rollback",
            "state": "rolled-back",
            "migration_id": manifest.get("migration_id"),
            "manifest": str(manifest_path),
            "actions": items,
            "preserved": manifest.get("preserved") or [],
            "writes": audit.writes,
            "launchctl_invoked": audit.launchctl_invoked,
            "message": "legacy program paths restored; services were not restarted",
        }


def _verify_manifest_item(
    item: object,
    home: Path,
    prefix: Path,
    link_dir: Path,
    backup_root: Path,
) -> None:
    if not isinstance(item, dict):
        raise MigrationError("migration manifest has a non-object item")
    raw_path = item.get("path")
    relative = item.get("relative_to_home")
    if not all(isinstance(value, str) for value in (raw_path, relative)):
        raise MigrationError("migration manifest has an invalid path item")
    relative_path = PurePosixPath(relative)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or ".." in relative_path.parts
        or relative_path.as_posix() != relative
    ):
        raise MigrationError("migration manifest has a non-canonical relative path")
    path = _absolute(raw_path)
    expected_path = home.joinpath(*relative_path.parts)
    if raw_path != str(path) or path != expected_path or not _path_under(path, home):
        raise MigrationError(f"migration item path mismatch: {path}")
    _validate_chain(home, path.parent)
    launch_agents_dir = _absolute(
        os.environ.get("RT_LAUNCH_AGENTS_DIR", home / "Library" / "LaunchAgents")
    )
    _validate_chain(home, launch_agents_dir)
    is_program = path.parent == prefix / "bin" and path.name in LEGACY_BIN_NAMES
    is_skill = path == prefix / "skills" / "shared" / "roundtable" / "SKILL.md"
    is_launch_agent = (
        path.parent == launch_agents_dir
        and path.name in {f"{label}.plist" for label in CODEX_LABELS}
    )
    is_command_link = path.parent == link_dir and path.name in LEGACY_TOOL_NAMES
    is_runtime_directory = path in {
        prefix / ".runtime",
        prefix / ".runtime" / "projects",
    }
    allowed = (
        is_program
        or is_skill
        or is_launch_agent
        or is_command_link
        or is_runtime_directory
    )
    if not allowed:
        raise MigrationError(f"migration item escapes supported paths: {path}")
    if item.get("kind") not in {"file", "symlink", "directory-mode"}:
        raise MigrationError("migration manifest has an invalid item kind")
    digest = item.get("sha256")
    if item["kind"] == "directory-mode":
        if (
            item.get("backup") is not None
            or digest is not None
            or item.get("target") is not None
            or item.get("mode") != 0o755
            or item.get("desired_mode") != 0o700
            or path not in {prefix / ".runtime", prefix / ".runtime" / "projects"}
        ):
            raise MigrationError("migration manifest has invalid directory-mode metadata")
        return
    if item["kind"] == "file" and not (is_program or is_skill or is_launch_agent):
        raise MigrationError("migration manifest has a file on a non-file migration path")
    if item["kind"] == "symlink" and not is_command_link:
        raise MigrationError("migration manifest has a symlink on a non-link migration path")
    raw_backup = item.get("backup")
    if not isinstance(raw_backup, str):
        raise MigrationError("migration manifest has an invalid backup path")
    backup = _absolute(raw_backup)
    expected_backup = backup_root / "payload" / Path(*relative_path.parts)
    if raw_backup != str(backup) or backup != expected_backup:
        raise MigrationError(f"migration backup is not canonical for its source: {backup}")
    _validate_chain(home, backup.parent)
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise MigrationError("migration manifest has an invalid digest")
    if item["kind"] == "file":
        mode = item.get("mode")
        if (
            not isinstance(mode, int)
            or not 0 <= mode <= 0o7777
            or item.get("target") is not None
            or item.get("desired_mode") is not None
        ):
            raise MigrationError("migration manifest has invalid file metadata")
    else:
        target = item.get("target")
        try:
            target_digest = (
                _sha256_bytes(target.encode("utf-8"))
                if isinstance(target, str)
                else None
            )
        except UnicodeEncodeError as error:
            raise MigrationError("migration manifest has an invalid symlink target") from error
        if (
            not isinstance(target, str)
            or item.get("mode") is not None
            or item.get("desired_mode") is not None
            or digest != target_digest
        ):
            raise MigrationError("migration manifest has invalid symlink metadata")
        expected_target = prefix / "bin" / path.name
        try:
            resolved_target = (path.parent / target).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as error:
            raise MigrationError("migration manifest has an invalid symlink target") from error
        if target == str(expected_target) or resolved_target != expected_target:
            raise MigrationError(
                "migration manifest symlink target does not match its legacy command"
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roundtable-migrate",
        description="Plan, apply, or roll back a conservative pre-manifest migration.",
    )
    parser.add_argument("command", nargs="?", choices=("plan", "apply", "rollback"), default="plan")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--link-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def _render(result: dict) -> str:
    lines = [
        f"legacy migration: {result.get('state')}",
        f"manifest: {result.get('manifest')}",
        f"writes: {'yes' if result.get('writes') else 'no'}",
    ]
    actions = result.get("actions") or []
    if actions:
        lines.append("paths:")
        for item in actions:
            lines.append(f"  - {item['path']} ({item['reason']})")
    if result.get("message"):
        lines.append(str(result["message"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    audit = OperationAudit()
    try:
        home = _absolute(args.home)
        prefix = _absolute(args.prefix) if args.prefix else home / ".roundtable"
        link_dir = _absolute(args.link_dir) if args.link_dir else home / ".local" / "bin"
        if args.command == "rollback":
            result = _rollback(home, prefix, audit)
        elif args.command == "apply":
            result = _apply(home, prefix, link_dir, audit)
        else:
            result = {**_plan(home, prefix, link_dir), "command": "plan"}
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else _render(result))
        return 0
    except (MigrationError, OSError) as error:
        if args.json:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "command": args.command,
                        "error": str(error),
                        "writes": audit.writes,
                        "launchctl_invoked": audit.launchctl_invoked,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"roundtable-migrate: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
