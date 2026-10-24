"""Successor acceptance: actual subprocess failure, owner-issued resolution, real repair."""

import importlib.util
import json
import math
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest


def api():
    assert importlib.util.find_spec("acp_conductor_resolution") is not None, (
        "missing version-isolated UNKNOWN-resolution owner"
    )
    import acp_conductor_resolution as module

    return module


def adapter(code):
    return api().SubprocessAdapter((sys.executable, "-I", "-c", code))


def request(task, runner, expected="REPAIRED", timeout=2):
    return api().Request(task, runner.identity, "mixed Case", expected, timeout=timeout)


def submit(store, task, runner, **kwargs):
    req = request(task, runner, **kwargs)
    return store.submit(task, req, api().Grant(req.digest, req.adapter_id, time.time() + 60))


def resolution(store, attempt):
    evidence = store.resolution_evidence(attempt)
    row = store.query(attempt)
    return api().ResolutionGrant(
        attempt, row["digest"], evidence["evidence_digest"], time.time() + 60
    )


@pytest.mark.parametrize("code", ["import sys;sys.exit(7)", "print('WRONG')"])
def test_real_failure_resolution_replan_and_separately_granted_success(tmp_path, code):
    from acp_conductor_experiment import Task
    from acp_conductor_replanning import Approval, Journal, Observation

    module = api()
    store = module.Store(tmp_path / "runtime", max_starts=2, max_running=1)
    journal = Journal(
        tmp_path / "plan.db", initial=(Task("broken"),), max_rounds=1, max_revisions=1
    )
    broken = adapter(code)
    old = submit(store, "broken", broken)
    journal.bind("broken", old)
    store.run(old, broken)
    assert store.verify(old)["state"] == "UNKNOWN"
    journal.reserve("repair")
    proposal = journal.offer(
        "repair",
        json.dumps({"version": 1, "tasks": [{"task_id": "repaired", "dependencies": []}]}).encode(),
    )
    approval = Approval(proposal["base_digest"], proposal["candidate_digest"], time.time() + 60)
    with pytest.raises(ValueError, match="uncertain"):
        journal.adopt(
            "approval",
            "repair",
            approval,
            (Observation("broken", old, store.query(old)["state"]),),
            now=time.time(),
        )
    grant = resolution(store, old)
    assert store.resolve(old, grant)["state"] == "FAILED"
    assert "UNKNOWN" in [event["state"] for event in store.history(old)]
    # No forged projection: reopen the actual new owner and read its durable state.
    store = module.Store(tmp_path / "runtime", max_starts=2, max_running=1)
    adopted = journal.adopt(
        "approval",
        "repair",
        approval,
        (Observation("broken", old, store.query(old)["state"]),),
        now=time.time(),
    )
    assert adopted["revision"] == 1
    good = adapter("print('REPAIRED')")
    req = request("repaired", good)
    with pytest.raises(PermissionError):
        store.submit("missing-grant", req, approval)
    new = submit(store, "repaired", good)
    journal.bind("repaired", new)
    assert store.run(new, good)["state"] == "CANDIDATE"
    assert store.verify(new)["state"] == "VERIFIED"
    assert store.artifact(new).strip() == b"REPAIRED"
    assert store.stats()["starts"] == 2
    assert store.run(old, broken)["state"] == "FAILED"
    assert store.stats()["starts"] == 2
    assert journal.snapshot()["bindings"] == {"broken": old, "repaired": new}
    # Exact replay reads history, while a changed grant tuple is a conflict.
    assert store.resolve(old, grant)["state"] == "FAILED"
    with pytest.raises(PermissionError):
        store.resolve(old, replace(grant, expires_at=0))
    assert sum(e["state"] == "FAILED" for e in store.history(old)) == 1


@pytest.mark.parametrize(
    "change", ["none", "execution", "attempt", "request", "evidence", "expired", "bool", "nan"]
)
def test_resolution_negative_bindings_leave_history_slots_and_starts_unchanged(tmp_path, change):
    m = api()
    store = m.Store(tmp_path / "runtime")
    runner = adapter("raise SystemExit(2)")
    attempt = submit(store, "broken", runner)
    store.run(attempt, runner)
    good = resolution(store, attempt)
    bad = {
        "none": None,
        "execution": m.Grant(good.request_digest, runner.identity, time.time() + 60),
        "attempt": replace(good, attempt_id="wrong"),
        "request": replace(good, request_digest="0" * 64),
        "evidence": replace(good, evidence_digest="0" * 64),
        "expired": replace(good, expires_at=0),
        "bool": replace(good, expires_at=True),
        "nan": replace(good, expires_at=math.nan),
    }[change]
    before = (store.history(attempt), store.stats())
    with pytest.raises((ValueError, PermissionError)):
        store.resolve(attempt, bad)
    assert (store.history(attempt), store.stats()) == before
    assert store.query(attempt)["state"] == "UNKNOWN"
    assert store.stats()["active"] == 1


def test_budget_not_refunded_by_resolution_or_reopen(tmp_path):
    m = api()
    root = tmp_path / "runtime"
    store = m.Store(root, max_starts=1, max_running=1)
    bad = adapter("raise SystemExit(2)")
    old = submit(store, "old", bad)
    store.run(old, bad)
    good = adapter("print('REPAIRED')")
    new = submit(store, "new", good)
    with pytest.raises(ValueError, match="budget"):
        store.run(new, good)
    store.resolve(old, resolution(store, old))
    store = m.Store(root, max_starts=1, max_running=1)
    with pytest.raises(ValueError, match="budget"):
        store.run(new, good)
    assert store.query(new)["started"] == 0
    assert store.stats() == {"starts": 1, "active": 0}
    with pytest.raises(ValueError, match="budget"):
        m.Store(root, max_starts=2, max_running=1)
    with pytest.raises(ValueError, match="attempt"):
        submit(store, "old", good)


def test_v1_root_refused_before_v2_writes(tmp_path):
    from acp_conductor_runtime import Store as OldStore

    root = tmp_path / "runtime"
    OldStore(root)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ValueError, match="v1"):
        api().Store(root)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize("runtime", ["python", "node"])
@pytest.mark.parametrize("mode", ["success", "nonzero", "mismatch", "timeout"])
def test_real_language_runtime_contract(tmp_path, runtime, mode):
    m = api()
    if runtime == "node":
        executable = shutil.which("node")
        if executable is None:
            pytest.skip("Node.js unavailable: not a second Agent runtime claim")
        code = {
            "success": "console.log('REPAIRED')",
            "nonzero": "process.exit(7)",
            "mismatch": "console.log('wrong')",
            "timeout": "setTimeout(()=>{}, 10000)",
        }[mode]
        runner = m.SubprocessAdapter((executable, "-e", code))
    else:
        code = {
            "success": "print('REPAIRED')",
            "nonzero": "raise SystemExit(7)",
            "mismatch": "print('wrong')",
            "timeout": "import time;time.sleep(10)",
        }[mode]
        runner = adapter(code)
    store = m.Store(tmp_path / "runtime", max_starts=2)
    attempt = submit(store, "test", runner, timeout=0.25 if mode == "timeout" else 2)
    store.run(attempt, runner)
    row = store.verify(attempt)
    if mode == "success":
        assert row["state"] == "VERIFIED"
        assert store.artifact(attempt).strip() == b"REPAIRED"
        with pytest.raises(ValueError):
            store.resolution_evidence(attempt)
    else:
        assert row["state"] == "UNKNOWN"
        grant = resolution(store, attempt)
        assert store.resolve(attempt, grant)["state"] == "FAILED"
    assert store.run(attempt, runner)["state"] == ("VERIFIED" if mode == "success" else "FAILED")
    assert store.stats()["starts"] == 1


def test_fresh_artifact_corruption_never_promotes_completion(tmp_path):
    m = api()
    store = m.Store(tmp_path / "runtime")
    good = adapter("print('REPAIRED')")
    attempt = submit(store, "good", good)
    store.run(attempt, good)
    row = store.verify(attempt)
    assert row["state"] == "VERIFIED"
    Path(row["artifact_path"]).write_bytes(b"corrupt")
    assert store.query(attempt)["state"] == "UNKNOWN"
    with pytest.raises(ValueError):
        store.resolution_evidence(attempt)
    assert store.verify(attempt)["state"] == "UNKNOWN"
    assert store.resolve(attempt, resolution(store, attempt))["state"] == "FAILED"
    with pytest.raises(ValueError):
        store.artifact(attempt)
