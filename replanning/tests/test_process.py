"""Real SQLite races and killed fresh-process proposal reservation."""

import json
import os
import select
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from acp_conductor_experiment import Task

from acp_conductor_replanning import Journal


def test_transactional_reservation_cap_under_race(tmp_path):
    path = tmp_path / "journal.db"

    def reserve(index):
        journal = Journal(path, initial=(Task("task"),), max_rounds=1, max_revisions=1)
        try:
            return journal.reserve(f"round{index}")["created"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1
    assert (
        Journal(path, initial=(Task("task"),), max_rounds=1, max_revisions=1).snapshot()[
            "rounds_used"
        ]
        == 1
    )


def test_killed_process_claim_is_not_replayed_or_refunded(tmp_path):
    path = tmp_path / "journal.db"
    conductor = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(str(conductor / p) for p in ("src", "replanning/src")),
    }
    script = """import sys,time
from acp_conductor_experiment import Task
from acp_conductor_replanning import Journal
j=Journal(sys.argv[1],initial=(Task("task"),),max_rounds=1,max_revisions=1)
r=j.reserve("round1")
print("CLAIMED",flush=True)
time.sleep(60)
"""
    process = subprocess.Popen(
        (sys.executable, "-c", script, str(path)),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([process.stdout], [], [], 8)
        assert ready and process.stdout.readline().strip() == "CLAIMED"
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=3) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
    check = """import sys,json
from acp_conductor_experiment import Task
from acp_conductor_replanning import Journal
j=Journal(sys.argv[1],initial=(Task("task"),),max_rounds=1,max_revisions=1)
r=j.reserve("round1")
assert not r["created"] and r["state"]=="CLAIMED"
j.close_unknown("round1")
try:j.reserve("round2")
except ValueError:pass
else:raise AssertionError("refund")
print(json.dumps(j.snapshot()))
"""
    result = subprocess.run(
        (sys.executable, "-c", check, str(path)),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["rounds_used"] == 1
