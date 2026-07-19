#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
bootstrap_python=${ROUNDTABLE_BOOTSTRAP_PYTHON:-python3}

PYTHONPATH="$source_root${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$bootstrap_python" -m roundtable_packaging.cli uninstall "$@"
