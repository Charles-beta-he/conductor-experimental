"""Authorization and conservative closure unit probes; not real-runtime evidence."""

import concurrent.futures
import os
import sqlite3
import time
from dataclasses import replace

import pytest
from test_resolution import adapter, api, resolution, submit


@pytest.mark.parametrize("state", ["PREPARED", "CANDIDATE", "VERIFIED", "CANCELLED"])
def test_only_persisted_unknown_can_resolve(tmp_path, state):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("print('REPAIRED')")
    attempt = submit(store, "one", runner)
    if state in {"CANDIDATE", "VERIFIED"}:
        store.run(attempt, runner)
    if state == "VERIFIED":
        store.verify(attempt)
    if state == "CANCELLED":
        store.cancel(attempt)
    before = store.history(attempt), store.stats()
    grant = m.ResolutionGrant(attempt, store.query(attempt)["digest"], "0" * 64, time.time() + 60)
    with pytest.raises((ValueError, PermissionError)):
        store.resolve(attempt, grant)
    assert (store.history(attempt), store.stats()) == before


def test_ambiguous_adapter_exception_cannot_attest_closure(tmp_path):
    m = api()
    store = m.Store(tmp_path / "runtime")
    real = adapter("print('REPAIRED')")

    class Ambiguous:
        identity = real.identity

        def execute_authorized(self, request, workspace, grant):
            raise RuntimeError("private credential never persisted")

    attempt = submit(store, "one", real)
    assert store.run(attempt, Ambiguous())["state"] == "UNKNOWN"
    with pytest.raises(ValueError, match="closed"):
        store.resolution_evidence(attempt)
    assert "private credential" not in str(store.history(attempt))


def test_group_absence_not_established_never_attests_closed(tmp_path, monkeypatch):
    m = api()
    from acp_conductor_resolution import process

    original = os.killpg

    def ambiguous_group(group, sig):
        if sig == 0:
            raise PermissionError("cannot establish group absence")
        return original(group, sig)

    monkeypatch.setattr(process.os, "killpg", ambiguous_group)
    store = m.Store(tmp_path / "runtime")
    runner = adapter("print('REPAIRED')")
    attempt = submit(store, "one", runner)
    assert store.run(attempt, runner)["state"] == "UNKNOWN"
    with pytest.raises(ValueError, match="closed"):
        store.resolution_evidence(attempt)


def test_expiry_checked_after_resolution_lock_acquired(tmp_path):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    attempt = submit(store, "one", runner)
    store.run(attempt, runner)
    before = store.history(attempt), store.stats()
    grant = replace(resolution(store, attempt), expires_at=time.time() + 0.2)
    lock = sqlite3.connect(store.db, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(store.resolve, attempt, grant)
            time.sleep(0.3)
            lock.rollback()
            with pytest.raises(PermissionError):
                result.result(timeout=5)
    finally:
        lock.close()
    assert (store.history(attempt), store.stats()) == before


def test_committed_exact_grant_readback_after_wall_clock_expiry(tmp_path, monkeypatch):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    attempt = submit(store, "one", runner)
    store.run(attempt, runner)
    grant = resolution(store, attempt)
    store.resolve(attempt, grant)
    before = store.history(attempt), store.stats()
    monkeypatch.setattr(time, "time", lambda: grant.expires_at + 1)
    assert store.resolve(attempt, grant)["state"] == "FAILED"
    assert (store.history(attempt), store.stats()) == before


def test_resolution_transaction_rollback_has_no_partial_events(tmp_path):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    attempt = submit(store, "one", runner)
    store.run(attempt, runner)
    grant = resolution(store, attempt)
    before = store.history(attempt), store.stats()
    # Disposable test DB fault injection only: abort the FAILED event insert.
    conn = sqlite3.connect(store.db)
    conn.execute(
        "CREATE TRIGGER fault BEFORE INSERT ON events WHEN NEW.state='FAILED' "
        "BEGIN SELECT RAISE(ABORT,'test injected crash boundary'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        store.resolve(attempt, grant)
    assert (store.history(attempt), store.stats()) == before
    conn.execute("DROP TRIGGER fault")
    conn.commit()
    conn.close()
    assert store.resolve(attempt, grant)["state"] == "FAILED"


@pytest.mark.parametrize("resolved", [False, True])
def test_new_attempt_key_cannot_rebind_same_task(tmp_path, resolved):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    old = submit(store, "immutable-task", runner)
    store.run(old, runner)
    if resolved:
        store.resolve(old, resolution(store, old))
    req = m.Request("immutable-task", runner.identity, "different request", "REPAIRED")
    grant = m.Grant(req.digest, req.adapter_id, time.time() + 60)
    before = store.inventory(), store.history(old), store.stats()
    with pytest.raises(ValueError, match="task"):
        store.submit("different-key", req, grant)
    assert (store.inventory(), store.history(old), store.stats()) == before


def test_evidence_explicit_adapter_and_call_token_binding(tmp_path):
    store = api().Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    old = submit(store, "old", runner)
    store.run(old, runner)
    evidence = store.resolution_evidence(old)
    assert evidence["adapter_id"] == runner.identity
    assert evidence["call_token"] == store.query(old)["token"]
    assert evidence["closure"]["token"] == evidence["call_token"]


@pytest.mark.parametrize("mode", ["present", "wait_failure"])
def test_cleanup_uncertainty_has_no_closure_evidence(tmp_path, monkeypatch, mode):
    from acp_conductor_resolution import process

    m = api()
    killpg, wait = os.killpg, process.subprocess.Popen.wait
    if mode == "present":

        def still_present(group, sig):
            return None if sig == 0 else killpg(group, sig)

        monkeypatch.setattr(process.os, "killpg", still_present)
    else:

        def fail_after_reaping(proc, *args, **kwargs):
            wait(proc, *args, **kwargs)
            raise OSError("injected cleanup failure after real reap")

        monkeypatch.setattr(process.subprocess.Popen, "wait", fail_after_reaping)
    store = m.Store(tmp_path / "runtime")
    runner = adapter("print('REPAIRED')")
    attempt = submit(store, "one", runner)
    assert store.run(attempt, runner)["state"] == "UNKNOWN"
    with pytest.raises(ValueError, match="closed"):
        store.resolution_evidence(attempt)


def test_pinned_script_drift_rejects_before_process_start(tmp_path):
    import sys

    script = tmp_path / "worker.py"
    marker = tmp_path / "effect"
    script.write_text("print('REPAIRED')")
    m = api()
    runner = m.SubprocessAdapter((sys.executable, "-I", str(script)), pinned_files=(script,))
    store = m.Store(tmp_path / "runtime")
    attempt = submit(store, "one", runner)
    script.write_text(f"from pathlib import Path;Path({str(marker)!r}).touch();print('REPAIRED')")
    assert store.run(attempt, runner)["state"] == "UNKNOWN"
    assert not marker.exists()
    with pytest.raises(ValueError, match="closed"):
        store.resolution_evidence(attempt)
