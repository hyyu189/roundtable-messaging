"""Shared project selector for Roundtable harness launchers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _rtlib import emit_registry_warnings, is_project_root, load_project_registry


COMMANDS = {
    "claude": ["claude"],
    "codex": ["codex", "--remote", "unix://"],
    "hermes": ["hermes"],
}


class SelectionError(RuntimeError):
    pass


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
    command = [*COMMANDS[harness], *argv]
    os.execvp(command[0], command)
    return 127


def main(harness: str) -> int:
    try:
        return launch(harness, sys.argv[1:])
    except SelectionError as error:
        print(error, file=sys.stderr)
        return 2

