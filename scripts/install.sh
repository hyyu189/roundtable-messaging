#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
bootstrap_python=${ROUNDTABLE_BOOTSTRAP_PYTHON:-python3}

if ! command -v "$bootstrap_python" >/dev/null 2>&1; then
  echo "roundtable-install: CPython 3.11 through 3.14 is required; not found: $bootstrap_python" >&2
  echo "set ROUNDTABLE_BOOTSTRAP_PYTHON=/absolute/path/to/python3" >&2
  exit 1
fi
if ! "$bootstrap_python" -c 'import sys; raise SystemExit(0 if sys.implementation.name == "cpython" and (3, 11) <= sys.version_info[:2] <= (3, 14) else 1)' >/dev/null 2>&1; then
  echo "roundtable-install: $bootstrap_python must be CPython 3.11 through 3.14" >&2
  echo "set ROUNDTABLE_BOOTSTRAP_PYTHON=/absolute/path/to/a/supported/python3" >&2
  exit 1
fi

mode=source
for argument in "$@"; do
  case "$argument" in
    --wheel-dir|--wheel-dir=*)
      mode=wheel
      ;;
    --source-root|--source-root=*)
      mode=explicit
      ;;
  esac
done

if [ "$mode" = source ] && [ -d "$source_root/wheels" ]; then
  set -- --wheel-dir "$source_root/wheels" "$@"
elif [ "$mode" = source ]; then
  set -- --source-root "$source_root" "$@"
fi

PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$bootstrap_python" -m roundtable_packaging.cli \
  install "$@"
