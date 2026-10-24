"""M5 acceptance-first tests. Plan admission is not execution authority."""

import json

import pytest
from acp_conductor_experiment import Task

from acp_conductor_replanning import Approval, Journal, Observation


def proposal(*tasks):
    return json.dumps(
        {
            "version": 1,
            "tasks": [{"task_id": t.task_id, "dependencies": list(t.dependencies)} for t in tasks],
        }
    ).encode()


def create(tmp_path, rounds=3, revisions=2):
    return Journal(
        tmp_path / "plan.sqlite3",
        initial=(Task("old"),),
        max_rounds=rounds,
        max_revisions=revisions,
    )


def offer(journal, key="round1", tasks=None):
    if tasks is None:
        tasks = (Task("new"),)
    reserved = journal.reserve(key)
    assert reserved["created"] is True
    return journal.offer(key, proposal(*tasks))


def approve(journal, candidate):
    return Approval(journal.snapshot()["digest"], candidate["candidate_digest"], 100)


def test_rounds_persist_no_refund_or_automatic_replay(tmp_path):
    journal = create(tmp_path, rounds=2)
    first = journal.reserve("one")
    assert first["created"] and first["state"] == "CLAIMED"
    reopened = create(tmp_path, rounds=2)
    assert reopened.reserve("one")["created"] is False
    with pytest.raises(ValueError):
        reopened.reserve("two")
    reopened.close_unknown("one")
    assert reopened.snapshot()["rounds_used"] == 1
    reopened.reserve("two")
    reopened.close_unknown("two")
    with pytest.raises(ValueError):
        reopened.reserve("three")
    assert reopened.snapshot()["rounds_used"] == 2


def test_valid_plan_requires_separate_exact_approval_and_persists(tmp_path):
    journal = create(tmp_path)
    candidate = offer(journal)
    assert journal.snapshot()["revision"] == 0
    for approval in (
        Approval("0" * 64, candidate["candidate_digest"], 100),
        Approval(journal.snapshot()["digest"], "0" * 64, 100),
        Approval(journal.snapshot()["digest"], candidate["candidate_digest"], 10),
    ):
        with pytest.raises((ValueError, PermissionError)):
            journal.adopt("adopt1", "round1", approval, (), now=10)
    approved = approve(journal, candidate)
    adopted = journal.adopt("adopt1", "round1", approved, (), now=10)
    assert adopted["revision"] == 1 and adopted["tasks"] == [{"task_id": "new", "dependencies": []}]
    reopened = create(tmp_path)
    assert reopened.snapshot() == journal.snapshot()
    # Idempotent historical readback, not fresh execution/verification authority.
    assert reopened.adopt("adopt1", "round1", approved, (), now=1000) == adopted
    assert reopened.snapshot()["revision"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"version":true,"tasks":[]}',
        b'{"version":1,"version":1,"tasks":[]}',
        b'{"version":1,"tasks":[],"grant":true}',
        b"\xff",
        b"{} {}",
        b'{"version":NaN,"tasks":[]}',
        b'{"version":1,"tasks":[]}',
        b"x" * 65537,
        b'{"version":1,"tasks":[{"task_id":"x","dependencies":[],"code":"pass"}]}',
        b'{"version":1,"tasks":[{"task_id":"x","dependencies":["x"]}]}',
    ],
)
def test_bad_proposal_spends_round_without_mutating_plan(tmp_path, raw):
    journal = create(tmp_path)
    before = journal.snapshot()["digest"]
    journal.reserve("bad")
    with pytest.raises(ValueError):
        journal.offer("bad", raw)
    assert journal.snapshot()["rounds_used"] == 1
    assert journal.snapshot()["digest"] == before
    assert journal.reserve("next")["created"]


@pytest.mark.parametrize("state", ["UNKNOWN", "RUNNING", "PREPARED", "CANDIDATE"])
def test_unsafe_bound_history_cannot_be_dropped(tmp_path, state):
    journal = create(tmp_path)
    journal.bind("old", "attempt1")
    candidate = offer(journal)
    with pytest.raises(ValueError):
        journal.adopt(
            "adopt1",
            "round1",
            approve(journal, candidate),
            (Observation("old", "attempt1", state),),
            now=10,
        )
    assert journal.snapshot()["revision"] == 0


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_explicit_failed_retirement_preserves_binding_history(tmp_path, state):
    journal = create(tmp_path)
    journal.bind("old", "attempt1")
    candidate = offer(journal)
    observed = (Observation("old", "attempt1", state),)
    journal.adopt("adopt1", "round1", approve(journal, candidate), observed, now=10)
    later = offer(journal, "round2", (Task("newer"),))
    # An inactive/retired task still cannot be omitted or project UNKNOWN.
    for observations in ((), (Observation("old", "attempt1", "UNKNOWN"),)):
        with pytest.raises(ValueError):
            journal.adopt("adopt2", "round2", approve(journal, later), observations, now=10)
    journal.adopt("adopt2", "round2", approve(journal, later), observed, now=10)
    assert journal.snapshot()["revision"] == 2


def test_caps_cannot_change_on_reopen(tmp_path):
    journal = create(tmp_path)
    with pytest.raises(ValueError):
        create(tmp_path, rounds=4)
    assert journal.snapshot()["rounds_used"] == 0


@pytest.mark.parametrize("rounds,revisions", [(True, 2), (0, 2), (3, False), (3, 0), (3.0, 2)])
def test_strict_caps(tmp_path, rounds, revisions):
    with pytest.raises(ValueError):
        create(tmp_path, rounds, revisions)


def test_no_attempt_rebinding_or_structural_identity_reuse(tmp_path):
    journal = create(tmp_path)
    journal.bind("old", "attempt1")
    journal.bind("old", "attempt1")
    with pytest.raises(ValueError):
        journal.bind("old", "attempt2")
    with pytest.raises(ValueError):
        journal.bind("not_in_plan", "attempt1")
    candidate = offer(journal, tasks=(Task("new"), Task("old", ("new",))))
    with pytest.raises(ValueError):
        journal.adopt(
            "adopt1",
            "round1",
            approve(journal, candidate),
            (Observation("old", "attempt1", "FAILED"),),
            now=10,
        )


def test_unresolved_round_blocks_adoption_and_revision_cap_persists(tmp_path):
    journal = create(tmp_path, rounds=3, revisions=1)
    candidate = offer(journal)
    journal.reserve("inflight")
    with pytest.raises(ValueError):
        journal.adopt("adopt1", "round1", approve(journal, candidate), (), now=10)
    journal.close_unknown("inflight")
    journal.adopt("adopt1", "round1", approve(journal, candidate), (), now=10)
    reopened = create(tmp_path, rounds=3, revisions=1)
    candidate2 = offer(reopened, "round3", (Task("last"),))
    with pytest.raises(ValueError):
        reopened.adopt("adopt2", "round3", approve(reopened, candidate2), (), now=10)
