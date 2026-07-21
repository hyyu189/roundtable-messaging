"""Process-wide isolation for host-local Roundtable state.

Several modules resolve their default runtime directory at import time.  A
per-test fixture is therefore too late: a test that forgets to override one
module can leak synthetic leases into the user's real ``~/.roundtable``.  Set
the process environment before test modules are imported, then let focused
fixtures override it as needed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="roundtable-pytest-host-")).resolve()
_TEST_HOME = _TEST_ROOT / "home"
_TEST_HOME.mkdir(mode=0o700)
_TEST_RUNTIME = _TEST_ROOT / "runtime"
_TEST_RUNTIME.mkdir(mode=0o700)
os.environ["HOME"] = str(_TEST_HOME)
os.environ["CODEX_HOME"] = str(_TEST_HOME / ".codex")
os.environ["RT_RUNTIME_DIR"] = str(_TEST_RUNTIME)
os.environ["RT_CODEX_RUNTIME_DIR"] = str(_TEST_RUNTIME)
os.environ["RT_PROJECTS_FILE"] = str(_TEST_ROOT / "projects.yaml")
os.environ["RT_LAUNCH_AGENTS_DIR"] = str(_TEST_HOME / "Library" / "LaunchAgents")
# Installed launchers export this into child shells.  Tests import modules from
# the checkout and must never inherit an installed prefix or its live markers.
os.environ.pop("ROUNDTABLE_INSTALL_PREFIX", None)
for name in (
    "RT_CODEX_BIN",
    "CODEX_THREAD_ID",
    "CODEX_MANAGED_BY_NPM",
    "CODEX_MANAGED_PACKAGE_ROOT",
):
    os.environ.pop(name, None)
# A missed mock must fail without reaching the user's launchd domain.
os.environ["RT_LAUNCHCTL"] = "/usr/bin/false"


def pytest_sessionfinish(session, exitstatus) -> None:
    del session, exitstatus
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
