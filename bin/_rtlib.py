"""
Shared library for roundtable rt-* tools.
Portable version — no hardcoded paths or agent names.
"""
import json
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None


LIFECYCLE_ORDINAL = {"pending": 0, "injected": 1, "submitted": 2, "accepted": 3, "acked": 4}
PROJECTS_SCHEMA = "roundtable.projects.v1"


def projects_registry_path():
    override = os.environ.get("RT_PROJECTS_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".roundtable" / "projects.yaml"


def _empty_projects_doc():
    return {"schema": PROJECTS_SCHEMA, "projects": []}


def _read_projects_doc(path):
    if not path.exists():
        return _empty_projects_doc()
    if yaml is None:
        raise ValueError("PyYAML is required to read projects.yaml")
    try:
        loaded = yaml.safe_load(path.read_text())
    except Exception as error:
        # PyYAML's exception hierarchy is optional when yaml import failed, so
        # keep this parser boundary self-contained.
        raise ValueError(f"cannot parse {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} is not a mapping")
    if loaded.get("schema") != PROJECTS_SCHEMA:
        raise ValueError(
            f"{path} schema is {loaded.get('schema')!r}, expected {PROJECTS_SCHEMA!r}"
        )
    if not isinstance(loaded.get("projects"), list):
        raise ValueError(f"{path} projects is not a list")
    return loaded


def load_project_registry(path=None):
    """Return (valid entries, warnings), never deleting invalid entries.

    Each returned entry has a canonical absolute ``root`` Path and the original
    ``registered_at`` value. Consumers must use this function so stale registry
    entries are skipped consistently instead of growing their own discovery
    rules.
    """
    path = Path(path or projects_registry_path()).expanduser()
    try:
        document = _read_projects_doc(path)
    except ValueError as error:
        return [], [str(error)]
    valid = []
    warnings = []
    seen = set()
    for index, entry in enumerate(document.get("projects") or []):
        label = f"{path}: projects[{index}]"
        if not isinstance(entry, dict):
            warnings.append(f"{label} is not a mapping; skipped")
            continue
        value = entry.get("root")
        if not isinstance(value, str) or not value.strip():
            warnings.append(f"{label} has no root; skipped")
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            warnings.append(f"{label} root is not absolute: {value}; skipped")
            continue
        try:
            root = candidate.resolve()
        except OSError as error:
            warnings.append(f"{label} root cannot resolve: {error}; skipped")
            continue
        key = str(root)
        if key in seen:
            warnings.append(f"{label} duplicates {root}; skipped")
            continue
        registered_at = entry.get("registered_at")
        if not isinstance(registered_at, str) or not registered_at.strip():
            warnings.append(f"{label} has no valid registered_at; skipped")
            continue
        if not is_project_root(root):
            warnings.append(
                f"{label} missing {project_config_path(root)}; skipped"
            )
            continue
        seen.add(key)
        valid.append(
            {"root": root, "registered_at": registered_at}
        )
    return valid, warnings


def validate_project_registry(path=None):
    """Fail before a mutating workflow if the existing registry is malformed."""
    path = Path(path or projects_registry_path()).expanduser()
    try:
        _read_projects_doc(path)
    except ValueError as error:
        raise SystemExit(f"roundtable: invalid project registry: {error}")
    return path


def emit_registry_warnings(warnings, stream=None, tool="roundtable"):
    stream = stream or sys.stderr
    for warning in warnings:
        print(f"{tool}: registry warning: {warning}", file=stream)


def registered_project_roots(path=None, *, warn=True, tool="roundtable"):
    entries, warnings = load_project_registry(path)
    if warn:
        emit_registry_warnings(warnings, tool=tool)
    return [entry["root"] for entry in entries]


def _write_projects_doc(path, document):
    if yaml is None:
        raise SystemExit("roundtable: PyYAML is required to write projects.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    descriptor = None
    try:
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _update_project_registry(mutator, path=None):
    path = Path(path or projects_registry_path()).expanduser()
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            document = _read_projects_doc(path)
        except ValueError as error:
            raise SystemExit(f"roundtable: refusing to overwrite invalid registry: {error}")
        changed = mutator(document)
        if changed:
            _write_projects_doc(path, document)
        return changed


def register_project(root, path=None, registered_at=None):
    root = Path(root).expanduser().resolve()
    if not is_project_root(root):
        raise SystemExit(
            f"roundtable: not a project (missing {project_config_path(root)}): {root}"
        )
    timestamp = registered_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    def add(document):
        for entry in document["projects"]:
            if isinstance(entry, dict) and isinstance(entry.get("root"), str):
                try:
                    existing = Path(entry["root"]).expanduser().resolve()
                except OSError:
                    continue
                if existing == root:
                    return False
        document["projects"].append(
            {"root": str(root), "registered_at": timestamp}
        )
        return True

    return _update_project_registry(add, path)


def unregister_project(root, path=None):
    root = Path(root).expanduser().resolve()

    def remove(document):
        old = document["projects"]
        kept = []
        for entry in old:
            matches = False
            if isinstance(entry, dict) and isinstance(entry.get("root"), str):
                try:
                    matches = Path(entry["root"]).expanduser().resolve() == root
                except OSError:
                    matches = False
            if not matches:
                kept.append(entry)
        if len(kept) == len(old):
            return False
        document["projects"] = kept
        return True

    return _update_project_registry(remove, path)


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

    exact_matches = []
    legacy_matches = []
    for root in registered_project_roots(tool="roundtable"):
        runtime = root / ".roundtable" / "runtime.json"
        try:
            data = json.loads(runtime.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        runtime_ws_id = data.get("workspace_id")
        if runtime_ws_id:
            if ws_id and runtime_ws_id == ws_id:
                exact_matches.append(root)
            continue
        if ws_ref and data.get("workspace_ref") == ws_ref:
            legacy_matches.append(root)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        projects = ", ".join(str(root) for root in exact_matches)
        raise SystemExit(
            f"roundtable: multiple projects match caller workspace UUID {ws_id}: {projects}; "
            "set ROUNDTABLE_PROJECT_DIR explicitly"
        )
    if len(legacy_matches) == 1:
        return legacy_matches[0]
    if len(legacy_matches) > 1:
        projects = ", ".join(str(root) for root in legacy_matches)
        raise SystemExit(
            f"roundtable: multiple legacy projects match caller workspace ref {ws_ref}: {projects}; "
            "set ROUNDTABLE_PROJECT_DIR explicitly"
        )
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


def caller_workspace_ref(identify, tree=None):
    """Return only the real cmux caller's workspace ref.

    GUI focus is deliberately excluded: focused/active describe what the user is
    looking at, not which workspace launched the calling process.
    """
    return caller_cmux_context(identify, tree).get("workspace_ref") or ""


def current_workspace_ref(identify, tree=None):
    """Return caller/focused/active workspace for read-only display paths only."""
    for source in (identify or {}, tree or {}):
        for key in ("caller", "focused", "active"):
            value = source.get(key) or {}
            if value.get("workspace_ref"):
                return value.get("workspace_ref")
    return ""


def workspace_by_id(tree, workspace_id):
    """Find a cmux workspace by its stable UUID, returning (window, workspace)."""
    if not workspace_id:
        return None, None
    for window in (tree or {}).get("windows", []):
        for workspace in window.get("workspaces", []):
            if (workspace.get("id") or workspace.get("workspace_id")) == workspace_id:
                return window, workspace
    return None, None


def iter_ledgers(msg_dir):
    for path in sorted(Path(msg_dir).glob("*.jsonl")):
        if not path.is_file():
            continue
        if path.name.count("-") and path.stem.rsplit("-", 1)[-1].isdigit():
            continue
        yield path


def read_records(msg_dir):
    records = []
    for path in iter_ledgers(msg_dir):
        try:
            with path.open() as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("msg_id") and record.get("lifecycle") in LIFECYCLE_ORDINAL:
                        records.append(record)
        except (OSError, UnicodeError):
            # Maildir is the delivery fact source. One degraded optional ledger
            # must not make otherwise valid inbox mail unreadable.
            continue
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
