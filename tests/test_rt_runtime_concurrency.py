from __future__ import annotations

import multiprocessing
import os
import queue
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))

import _rtruntime


def _claim_worker(
    runtime: str,
    project: str,
    agent_id: str,
    ready,
    start,
    release,
    results,
) -> None:
    os.environ["RT_RUNTIME_DIR"] = runtime
    os.environ["RT_CODEX_RUNTIME_DIR"] = runtime
    ready.put(agent_id)
    if not start.wait(10):
        results.put((agent_id, "timeout", "start barrier"))
        return
    try:
        token = _rtruntime.claim(project, agent_id, "claude")
    except _rtruntime.SeatOccupied as error:
        owner = error.inspection.token
        results.put(
            (
                agent_id,
                "occupied",
                owner.agent_id if owner is not None else None,
            )
        )
        return
    except Exception as error:  # pragma: no cover - rendered for diagnosis
        results.put((agent_id, "error", repr(error)))
        return
    results.put((agent_id, "claimed", token.agent_id))
    release.wait(10)


def test_real_processes_cannot_claim_two_seats_for_one_harness(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    start = context.Event()
    release = context.Event()
    workers = [
        context.Process(
            target=_claim_worker,
            args=(
                str(runtime),
                str(project),
                agent_id,
                ready,
                start,
                release,
                results,
            ),
        )
        for agent_id in ("claude-build", "claude-review")
    ]
    for worker in workers:
        worker.start()
    try:
        assert {ready.get(timeout=10), ready.get(timeout=10)} == {
            "claude-build",
            "claude-review",
        }
        start.set()
        observed = [results.get(timeout=15), results.get(timeout=15)]
        claimed = [item for item in observed if item[1] == "claimed"]
        occupied = [item for item in observed if item[1] == "occupied"]

        assert len(claimed) == 1, observed
        assert len(occupied) == 1, observed
        assert occupied[0][2] == claimed[0][0]
    except queue.Empty as error:  # pragma: no cover - failure detail
        raise AssertionError("claim workers did not report before timeout") from error
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        assert all(worker.exitcode == 0 for worker in workers)
