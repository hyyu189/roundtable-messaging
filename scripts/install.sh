#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
bootstrap_python=${ROUNDTABLE_BOOTSTRAP_PYTHON:-python3}

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
