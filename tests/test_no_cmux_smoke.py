import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "smoke_no_cmux.py"


def test_no_cmux_smoke_uses_only_maildir_core():
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
    assert report["transport"] == "maildir"
    assert report["cmux"] == "absent"
    assert report["ack_files"] == 1
    assert report["recipient_inbox_after_drain"] == 0
