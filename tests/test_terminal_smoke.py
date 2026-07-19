import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "smoke_terminal.py"


def test_terminal_baseline_smoke_uses_only_maildir_core():
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, str(SMOKE), "--bin-dir", str(ROOT / "bin")],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert report["profile"] == "terminal-baseline"
    assert report["transport"] == "maildir"
    assert report["terminal_emulator"] == "not-required"
    assert report["optional_adapters_loaded"] == []
    assert report["ack_files"] == 1
    assert report["recipient_inbox_after_drain"] == 0
