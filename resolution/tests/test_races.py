"""Process-kill, concurrency and stale-result probes against real local processes."""

import concurrent.futures
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from test_resolution import adapter, api, resolution, submit


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("bounded probe deadline")


@pytest.mark.parametrize("operation", ["reconcile", "cancel"])
def test_live_reconcile_cannot_resolve_before_close_and_late_result_never_promotes(
    tmp_path, operation
):
    m = api()
    store = m.Store(tmp_path / "runtime", max_starts=2)
    runner = adapter("import time;time.sleep(.3);print('REPAIRED')")
    attempt = submit(store, "old", runner)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.run, attempt, runner)
        until(lambda: store.query(attempt)["state"] == "RUNNING")
        getattr(store, operation)(attempt)
        before = store.history(attempt)
        with pytest.raises(ValueError, match="closed"):
            store.resolution_evidence(attempt)
        forged = m.ResolutionGrant(
            attempt, store.query(attempt)["digest"], "0" * 64, time.time() + 60
        )
        with pytest.raises((ValueError, PermissionError)):
            store.resolve(attempt, forged)
        assert store.history(attempt) == before
        assert future.result(timeout=5)["state"] == "UNKNOWN"
    assert store.query(attempt)["artifact_path"] is None
    assert store.resolve(attempt, resolution(store, attempt))["state"] == "FAILED"
    assert store.stats()["starts"] == 1


def test_runner_sigkill_reopen_never_infers_closure_or_replays(tmp_path):
    m = api()
    root = tmp_path / "runtime"
    store = m.Store(root, max_starts=2)
    marker = tmp_path / "child.pid"
    runner = adapter(
        f"import os,time,pathlib;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(15)"
    )
    attempt = submit(store, "killed", runner, timeout=20)
    # This child runner imports the real candidate with the same configured PYTHONPATH.
    code = (
        "from acp_conductor_resolution import Store,SubprocessAdapter;"
        f"Store({str(root)!r},max_starts=2).run({attempt!r},SubprocessAdapter({runner.argv!r}))"
    )
    parent = subprocess.Popen(
        (sys.executable, "-c", code),
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = None
    try:
        until(marker.exists)
        child_pid = int(marker.read_text())
        assert store.query(attempt)["state"] == "RUNNING"
        parent.kill()
        assert parent.wait(timeout=5) == -signal.SIGKILL
        reopened = m.Store(root, max_starts=2)
        assert reopened.run(attempt, runner)["state"] == "RUNNING"
        assert reopened.reconcile(attempt)["state"] == "UNKNOWN"
        with pytest.raises(ValueError, match="closed"):
            reopened.resolution_evidence(attempt)
        assert reopened.run(attempt, runner)["state"] == "UNKNOWN"
        assert reopened.stats() == {"starts": 1, "active": 1}
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        parent.communicate(timeout=5)
        # Test-only cleanup of this probe's registered live child group. The product
        # must NEVER adopt/kill stored PIDs. Child has not exited while marker is read.
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_competing_resolution_and_start_are_idempotent(tmp_path):
    m = api()
    store = m.Store(tmp_path / "runtime", max_starts=2)
    runner = adapter("raise SystemExit(3)")
    old = submit(store, "old", runner)
    store.run(old, runner)
    grant = resolution(store, old)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: store.resolve(old, grant), range(12)))
    assert all(r["state"] == "FAILED" for r in rows)
    assert sum(e["state"] == "FAILED" for e in store.history(old)) == 1
    good = adapter("print('REPAIRED')")
    new = submit(store, "new", good)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.run(new, good), range(12)))
    assert store.verify(new)["state"] == "VERIFIED"
    assert store.stats()["starts"] == 2


@pytest.mark.parametrize("failure", ["raise SystemExit(3)", "print('WRONG')"])
def test_fresh_process_reads_same_resolution_and_starts(tmp_path, failure):
    m = api()
    root = tmp_path / "runtime"
    store = m.Store(root)
    runner = adapter(failure)
    old = submit(store, "old", runner)
    store.run(old, runner)
    store.verify(old)
    store.resolve(old, resolution(store, old))
    code = (
        "import json;from acp_conductor_resolution import Store;"
        f"s=Store({str(root)!r});print(json.dumps([s.query({old!r})['state'],s.stats(),s.history({old!r})]))"
    )
    result = subprocess.run(
        (sys.executable, "-c", code),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    state, stats, history = json.loads(result.stdout)
    assert state == "FAILED"
    assert stats == {"starts": 1, "active": 0}
    assert "UNKNOWN" in [e["state"] for e in history]


def test_successor_present_for_race_contract():
    assert importlib.util.find_spec("acp_conductor_resolution") is not None
