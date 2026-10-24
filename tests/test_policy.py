from dataclasses import asdict

import pytest

from acp_conductor_experiment import Attempt, Task, decide


def run(tasks=None, observations=(), **kwargs):
    return decide(
        (Task("build"), Task("check", ("build",))) if tasks is None else tasks,
        observations,
        attempts_used=kwargs.pop("attempts_used", len(observations)),
        max_attempts=kwargs.pop("max_attempts", 3),
        max_running=kwargs.pop("max_running", 1),
        **kwargs,
    )


def test_dispatch_is_only_a_proposal():
    result = run()
    assert (result.kind, result.task_id, result.attempt_id) == (
        "dispatch_proposal", "build", None
    )
    assert "completed" not in asdict(result)


def test_candidate_is_not_verified_and_blocks_dependency():
    result = run(observations=(Attempt("build", "a1", "CANDIDATE"),))
    assert (result.kind, result.task_id) == ("verify_request", "build")


def test_verified_trusted_projection_unblocks_dependency():
    result = run(observations=(Attempt("build", "a1", "VERIFIED"),))
    assert (result.kind, result.task_id) == ("dispatch_proposal", "check")


def test_accepted_projection_does_not_issue_completion():
    result = run(observations=(
        Attempt("build", "a1", "VERIFIED"), Attempt("check", "a2", "VERIFIED")
    ))
    assert result.kind == "accepted_projection"
    assert result.task_id is None


@pytest.mark.parametrize("state,kind", [
    ("UNKNOWN", "reconcile_required"), ("FAILED", "halt"), ("CANCELLED", "halt"),
    ("RUNNING", "wait"),
])
def test_non_success_never_dispatches_retry(state, kind):
    assert run(observations=(Attempt("build", "a1", state),)).kind == kind


def test_unknown_precedes_candidates_and_budget():
    tasks = (Task("one"), Task("two"))
    observations = (Attempt("one", "a1", "CANDIDATE"), Attempt("two", "a2", "UNKNOWN"))
    result = run(tasks, observations, max_attempts=2)
    assert (result.kind, result.task_id) == ("reconcile_required", "two")


def test_budget_exhausted_forbids_new_dispatch_but_not_verification():
    assert run(attempts_used=3).kind == "halt"
    assert run(observations=(Attempt("build", "a1", "CANDIDATE"),),
               attempts_used=3).kind == "verify_request"


def test_concurrency_limit_counts_other_running_tasks():
    tasks = (Task("one"), Task("two"))
    obs = (Attempt("one", "a1", "RUNNING"),)
    assert run(tasks, obs).kind == "wait"
    assert run(tasks, obs, max_running=2).task_id == "two"


def test_restart_reconstructed_projection_has_no_redispatch():
    import json
    raw = json.loads(json.dumps([{"task_id": "build", "attempt_id": "a1", "state": "UNKNOWN"}]))
    replay = tuple(Attempt(**item) for item in raw)
    assert run(observations=replay) == run(observations=replay)
    assert run(observations=replay).kind == "reconcile_required"


@pytest.mark.parametrize("tasks", [
    (), (Task("same"), Task("same")), (Task("x", ("missing",)),),
    (Task("x", ("x",)),), (Task("x", ("y",)), Task("y", ("x",))),
    (Task("x", ("y", "y")), Task("y")), (Task("bad id"),),
    (Task("x", ["y"]), Task("y")), [Task("x")], ("not-task",),
])
def test_invalid_plan_rejected(tasks):
    with pytest.raises(ValueError, match="CONDUCTOR_INPUT_INVALID"):
        run(tasks)


@pytest.mark.parametrize("observations", [
    (Attempt("unknown", "a1", "VERIFIED"),),
    (Attempt("build", "a1", "done"),),
    (Attempt("build", "a1", "VERIFIED"), Attempt("build", "a2", "RUNNING")),
    (Attempt("build", "same", "VERIFIED"), Attempt("check", "same", "RUNNING")),
    (Attempt("check", "a1", "RUNNING"),),
    (Attempt("build", "bad id", "RUNNING"),),
    [{"task_id": "build", "attempt_id": "a1", "state": "VERIFIED"}],
    (object(),),
])
def test_invalid_or_contradictory_projection_rejected(observations):
    with pytest.raises(ValueError, match="CONDUCTOR_INPUT_INVALID"):
        run(observations=observations)


@pytest.mark.parametrize("kwargs", [
    {"attempts_used": -1}, {"attempts_used": True}, {"max_attempts": 0},
    {"max_running": 0}, {"max_attempts": "3"}, {"max_running": False},
    {"attempts_used": 0, "observations": (Attempt("build", "a1", "RUNNING"),)},
])
def test_invalid_budget_rejected(kwargs):
    with pytest.raises(ValueError, match="CONDUCTOR_INPUT_INVALID"):
        run(**kwargs)
