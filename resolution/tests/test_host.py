"""Reusable host must read all owner history, not trust caller-made projections."""

import json
import time
from pathlib import Path

import pytest
from test_resolution import adapter, api, request, resolution


def setup(tmp_path):
    from acp_conductor_experiment import Task
    from acp_conductor_replanning import Journal

    from acp_conductor_resolution.host import Host

    store = api().Store(tmp_path / "runtime", max_starts=3)
    journal = Journal(tmp_path / "plan.db", initial=(Task("old"),), max_rounds=2, max_revisions=2)
    return Host(store, journal), store, journal


def propose(journal, key, tasks):
    from acp_conductor_replanning import Approval

    journal.reserve(key)
    offered = journal.offer(
        key,
        json.dumps(
            {"version": 1, "tasks": [{"task_id": t, "dependencies": []} for t in tasks]}
        ).encode(),
    )
    return Approval(offered["base_digest"], offered["candidate_digest"], time.time() + 60)


def step(host, task, runner, expected="REPAIRED"):
    req = request(task, runner, expected)
    return host.step(task, req, api().Grant(req.digest, req.adapter_id, time.time() + 60), runner)


def test_reusable_host_real_failure_full_history_adopt_repair(tmp_path):
    host, store, journal = setup(tmp_path)
    bad = adapter("raise SystemExit(7)")
    assert step(host, "old", bad)["state"] == "UNKNOWN"
    approval = propose(journal, "repair", ["new"])
    with pytest.raises(ValueError, match="uncertain"):
        host.adopt("accept", "repair", approval)
    store.resolve("old", resolution(store, "old"))
    assert host.adopt("accept", "repair", approval)["revision"] == 1
    assert step(host, "new", adapter("print('REPAIRED')"))["state"] == "VERIFIED"
    assert host.step().kind == "accepted_projection"
    assert store.stats()["starts"] == 2


def test_retired_verified_artifact_corruption_blocks_adoption_and_execution(tmp_path):
    host, store, journal = setup(tmp_path)
    assert step(host, "old", adapter("print('REPAIRED')"))["state"] == "VERIFIED"
    approval = propose(journal, "repair", ["new"])
    host.adopt("accept", "repair", approval)
    Path(store.query("old")["artifact_path"]).write_bytes(b"corrupt")
    before = store.stats()
    with pytest.raises(ValueError, match="uncertain"):
        step(host, "new", adapter("print('REPAIRED')"))
    # Historical adoption replay must not bypass a newly broken retired attempt.
    with pytest.raises(ValueError, match="uncertain"):
        host.adopt("accept", "repair", approval)
    assert store.stats()["starts"] == before["starts"]
    assert store.stats()["active"] == 1  # corrupted old execution retains its slot
    assert store.query("old")["state"] == "UNKNOWN"


def test_submit_bind_interruption_blocks_until_explicit_binding_readback(tmp_path, monkeypatch):
    host, store, journal = setup(tmp_path)
    bind = journal.bind
    monkeypatch.setattr(
        journal, "bind", lambda *args: (_ for _ in ()).throw(RuntimeError("test fault"))
    )
    runner = adapter("print('REPAIRED')")
    with pytest.raises(RuntimeError):
        step(host, "old", runner)
    assert store.query("old")["state"] == "PREPARED"
    assert store.stats()["starts"] == 0
    monkeypatch.setattr(journal, "bind", bind)
    with pytest.raises(ValueError, match="unbound"):
        step(host, "old", runner)
    # Deliberate trusted-host recovery through the existing public Journal interface.
    journal.bind("old", "old")
    req = request("old", runner)
    original_grant = api().Grant(**json.loads(store.query("old")["grant"]))
    assert host.step("old", req, original_grant, runner)["state"] == "VERIFIED"


def test_frozen_flow_rejects_successor_store(tmp_path):
    from acp_conductor_composition import Flow
    from acp_conductor_experiment import Task

    with pytest.raises(ValueError):
        Flow(api().Store(tmp_path / "runtime"), (Task("old"),), {})
