"""Shared project selector for Roundtable harness launchers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _rtlib import (
    emit_registry_warnings,
    is_project_root,
    load_agents_doc,
    load_project_registry,
)


COMMANDS = {
    "claude": ["claude"],
    "codex": ["codex", "--remote", "unix://"],
    "hermes": ["hermes"],
}
EXECUTABLE_OVERRIDES = {
    "claude": "RT_CLAUDE_BIN",
    "hermes": "RT_HERMES_BIN",
}
CONFIG_HARNESSES = {
    "claude": frozenset({"claude", "claude-code"}),
    "codex": frozenset({"codex"}),
    "hermes": frozenset({"hermes", "hermes-agent"}),
}
CMUX_SHIM_PARTS = frozenset({"cmux-cli-shims"})
CMUX_WRAPPER_NAMES = frozenset({"cmux-claude-wrapper", "cmux-codex-wrapper"})


class SelectionError(RuntimeError):
    pass


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _is_cmux_shim(path: Path) -> bool:
    candidates = [path.absolute()]
    try:
        candidates.append(path.resolve())
    except OSError:
        pass
    for candidate in candidates:
        parts = {part.lower() for part in candidate.parts}
        if parts & CMUX_SHIM_PARTS:
            return True
        if candidate.name.lower() in CMUX_WRAPPER_NAMES:
            return True
    return False


def harness_bin(harness: str) -> Path:
    """Resolve a real harness executable without depending on cmux PATH shims."""
    if harness == "codex":
        try:
            from _rtcodex import CodexRuntimeError, codex_bin

            return codex_bin()
        except CodexRuntimeError as error:
            raise SelectionError(f"rt-codex: {error}") from error

    override_name = EXECUTABLE_OVERRIDES[harness]
    override = os.environ.get(override_name)
    if override:
        selected = Path(override).expanduser().absolute()
        if _executable(selected) and not _is_cmux_shim(selected):
            return selected
        if _executable(selected):
            raise SelectionError(
                f"rt-{harness}: {override_name} points to a cmux wrapper: {selected}"
            )
        raise SelectionError(
            f"rt-{harness}: {override_name} is not executable: {selected}"
        )

    executable_name = COMMANDS[harness][0]
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / executable_name,
        home / ".npm-global" / "bin" / executable_name,
    ]
    if harness == "hermes":
        candidates.append(
            home / ".hermes" / "hermes-agent" / "venv" / "bin" / executable_name
        )
    candidates.extend(
        Path(directory).expanduser() / executable_name
        for directory in os.environ.get("PATH", "").split(os.pathsep)
        if directory
    )
    seen = set()
    for candidate in candidates:
        selected = candidate.absolute()
        key = str(selected)
        if key in seen:
            continue
        seen.add(key)
        if _executable(selected) and not _is_cmux_shim(selected):
            return selected
    raise SelectionError(
        f"rt-{harness}: could not find a non-cmux {executable_name} executable; "
        f"set {override_name}"
    )


def configured_sender_ids(root: Path, harness: str) -> list[str]:
    document = load_agents_doc(root, f"rt-{harness}")
    if not isinstance(document, dict):
        raise SelectionError(
            f"rt-{harness}: {root / '.roundtable' / 'agents.yaml'} is not a mapping"
        )
    agents = document.get("agents") or {}
    if not isinstance(agents, dict):
        raise SelectionError(
            f"rt-{harness}: agents in {root / '.roundtable' / 'agents.yaml'} "
            "is not a mapping"
        )
    ids = []
    for agent_name, config in agents.items():
        if not isinstance(config, dict):
            continue
        if config.get("harness") not in CONFIG_HARNESSES[harness]:
            continue
        instances = config.get("instances")
        if not isinstance(instances, list) or not instances:
            instances = [{"id": agent_name}]
        for instance in instances:
            instance_id = (
                instance.get("id") if isinstance(instance, dict) else instance
            )
            if isinstance(instance_id, str) and instance_id and instance_id not in ids:
                ids.append(instance_id)
    return ids


def set_launch_identity(root: Path | None, harness: str) -> None:
    if root is None or os.environ.get("RT_FROM"):
        return
    candidates = configured_sender_ids(root, harness)
    if len(candidates) == 1:
        os.environ["RT_FROM"] = candidates[0]
        return
    if len(candidates) > 1:
        rendered = ", ".join(candidates)
        raise SelectionError(
            f"rt-{harness}: multiple configured instances ({rendered}); "
            "set RT_FROM to the instance to launch"
        )


def project_at_or_above(start: Path) -> Path | None:
    current = start.expanduser().resolve()
    for candidate in (current, *current.parents):
        if is_project_root(candidate):
            return candidate
    return None


def _read_choice(stdin, stderr, prompt: str) -> str:
    print(prompt, end="", file=stderr, flush=True)
    value = stdin.readline()
    if value == "":
        raise SelectionError("input closed while selecting a Roundtable project")
    return value.strip()


def choose_launch_cwd(
    harness: str,
    *,
    cwd: Path | None = None,
    stdin=None,
    stderr=None,
    init_runner=subprocess.run,
) -> Path | None:
    """Return a root to chdir to, or None to preserve the current cwd."""
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    if project_at_or_above(cwd) is not None:
        return None
    if not stdin.isatty():
        raise SelectionError(
            f"rt-{harness}: not in a Roundtable project and stdin is not a TTY"
        )

    entries, warnings = load_project_registry()
    emit_registry_warnings(warnings, stream=stderr, tool=f"rt-{harness}")
    roots = [entry["root"] for entry in entries]
    print("Roundtable projects:", file=stderr)
    for index, root in enumerate(roots, 1):
        print(f"  {index}) {root}", file=stderr)
    create_index = len(roots) + 1
    unanchored_index = create_index + 1
    print(f"  {create_index}) Create a new project in {cwd}", file=stderr)
    print(f"  {unanchored_index}) Start without a project anchor", file=stderr)

    raw = _read_choice(stdin, stderr, "Select: ")
    try:
        selected = int(raw)
    except ValueError as error:
        raise SelectionError(f"rt-{harness}: invalid selection: {raw!r}") from error
    if 1 <= selected <= len(roots):
        return roots[selected - 1]
    if selected == create_index:
        name = _read_choice(stdin, stderr, "New project name: ")
        if not name:
            raise SelectionError(f"rt-{harness}: project name cannot be empty")
        init = Path(__file__).resolve().parent / "roundtable-init"
        result = init_runner([str(init), name, "--parent", str(cwd)], check=False)
        if result.returncode != 0:
            raise SelectionError(
                f"rt-{harness}: roundtable-init failed with exit {result.returncode}"
            )
        root = (cwd / name).resolve()
        if not is_project_root(root):
            raise SelectionError(
                f"rt-{harness}: roundtable-init did not create {root}"
            )
        return root
    if selected == unanchored_index:
        print(
            f"rt-{harness}: advisory: starting without a Roundtable project anchor from {cwd}",
            file=stderr,
        )
        return None
    raise SelectionError(f"rt-{harness}: selection out of range: {selected}")


def launch(harness: str, argv: list[str]) -> int:
    if harness not in COMMANDS:
        raise SelectionError(f"unknown Roundtable harness: {harness}")
    selected = choose_launch_cwd(harness)
    if selected is not None:
        os.chdir(selected)
    root = selected or project_at_or_above(Path.cwd())
    set_launch_identity(root, harness)
    command = [*COMMANDS[harness], *argv]
    command[0] = str(harness_bin(harness))
    os.execv(command[0], command)
    return 127


def main(harness: str) -> int:
    try:
        return launch(harness, sys.argv[1:])
    except SelectionError as error:
        print(error, file=sys.stderr)
        return 2
