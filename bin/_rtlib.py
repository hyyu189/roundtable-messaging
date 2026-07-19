"""
Shared library for roundtable rt-* tools.
Portable version — no hardcoded paths or agent names.
"""
import json
import os
import subprocess
import time
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None


LIFECYCLE_ORDINAL = {"pending": 0, "injected": 1, "submitted": 2, "accepted": 3, "acked": 4}


def project_config_path(root):
    return root / ".roundtable" / "agents.yaml"


def is_project_root(root):
    return project_config_path(root).is_file()


def fallback_project_root():
    """Fallback project when cwd isn't inside one and no workspace binding matches."""
    default = os.environ.get("RT_FALLBACK_PROJECT", "")
    if default:
        return Path(default).expanduser().resolve()
    return None


def not_project_message(tool):
    return (
        f"{tool}: not in a roundtable project; create .roundtable/agents.yaml, "
        "set ROUNDTABLE_PROJECT_DIR, or set RT_FALLBACK_PROJECT to a fallback project. "
        "Run 'roundtable-init <name>' to create a new project."
    )


def project_for_current_workspace():
    """Resolve the roundtable project bound to the caller's current cmux workspace.

    Matches by stable workspace_id (then ref) across all known project roots.
    Returns the project root Path, or None.
    """
    try:
        identify = json.loads(subprocess.check_output(
            ["cmux", "identify", "--json", "--id-format", "both"], text=True
        ))
    except Exception:
        return None
    caller = identify.get("caller") or {}
    ws_id, ws_ref = caller.get("workspace_id"), caller.get("workspace_ref")
    if not (ws_id or ws_ref):
        return None

    # Search common project locations
    search_roots = []
    # ROUNDTABLE_PROJECT_DIR's parent
    override = os.environ.get("ROUNDTABLE_PROJECT_DIR")
    if override:
        search_roots.append(Path(override).expanduser().resolve().parent)
    # RT_PROJECTS_DIR if set
    projects_dir = os.environ.get("RT_PROJECTS_DIR")
    if projects_dir:
        search_roots.append(Path(projects_dir).expanduser().resolve())
    # Fallback project's parent (sibling projects)
    fb = fallback_project_root()
    if fb:
        search_roots.append(fb.parent)

    for search_root in search_roots:
        for runtime in sorted(search_root.glob("*/.roundtable/runtime.json")):
            try:
                data = json.loads(runtime.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if (ws_id and data.get("workspace_id") == ws_id) or (
                ws_ref and data.get("workspace_ref") == ws_ref
            ):
                root = runtime.parent.parent
                if is_project_root(root):
                    return root
    return None


def find_project_root(tool):
    override = os.environ.get("ROUNDTABLE_PROJECT_DIR")
    if override:
        root = Path(override).expanduser().resolve()
        if is_project_root(root):
            return root
        raise SystemExit(not_project_message(tool))

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if is_project_root(candidate):
            return candidate

    bound = project_for_current_workspace()
    if bound is not None:
        return bound

    fallback = fallback_project_root()
    if fallback and is_project_root(fallback):
        return fallback

    raise SystemExit(not_project_message(tool))


def load_agents_doc(root, tool):
    if yaml is None:
        raise SystemExit(f"{tool}: PyYAML is required to read .roundtable/agents.yaml")
    with project_config_path(root).open() as fh:
        return yaml.safe_load(fh) or {}


def agent_names(agents_doc):
    """Extract agent names from agents.yaml (dynamic, not hardcoded)."""
    return list((agents_doc.get("agents") or {}).keys())


def run_json(*args):
    return json.loads(subprocess.check_output(args, text=True))


def caller_cmux_context(identify, tree=None):
    for source in (identify or {}, tree or {}):
        value = source.get("caller") or {}
        if value.get("workspace_ref"):
            return value
    return {}


def current_workspace_ref(identify, tree=None):
    for source in (identify or {}, tree or {}):
        for key in ("caller", "focused", "active"):
            value = source.get(key) or {}
            if value.get("workspace_ref"):
                return value.get("workspace_ref")
    return ""


def iter_ledgers(msg_dir):
    for path in sorted(Path(msg_dir).glob("*.jsonl")):
        if path.name.count("-") and path.stem.rsplit("-", 1)[-1].isdigit():
            continue
        yield path


def read_records(msg_dir):
    records = []
    for path in iter_ledgers(msg_dir):
        with path.open() as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("msg_id") and record.get("lifecycle") in LIFECYCLE_ORDINAL:
                    records.append(record)
    return records


def current_records(records):
    current = {}
    for record in records:
        msg_id = record.get("msg_id")
        rank = LIFECYCLE_ORDINAL.get(record.get("lifecycle"), -1)
        old = current.get(msg_id)
        old_rank = LIFECYCLE_ORDINAL.get((old or {}).get("lifecycle"), -1)
        if old is None or rank > old_rank or (
            rank == old_rank and record.get("ts", "") > old.get("ts", "")
        ):
            current[msg_id] = record
    return current


def has_lifecycle(records, msg_id, lifecycle):
    return any(
        record.get("msg_id") == msg_id and record.get("lifecycle") == lifecycle
        for record in records
    )


def find_msg(records, msg_id):
    matches = [record for record in records if record.get("msg_id") == msg_id]
    if not matches:
        return None
    return current_records(matches).get(msg_id)


def acquire_lock(path, timeout=10.0, tool="rt"):
    deadline = time.time() + timeout
    while True:
        try:
            path.mkdir(parents=True)
            return
        except FileExistsError:
            if time.time() >= deadline:
                raise SystemExit(f"{tool}: timed out waiting for lock {path}")
            time.sleep(0.05)
