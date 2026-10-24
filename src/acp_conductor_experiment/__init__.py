# SPDX-License-Identifier: Apache-2.0
"""Pure scheduling recommendations. Caller projections are trusted, not authenticated.

No execution, persistence, evidence verification, authority or lifecycle ownership.
A dispatch_proposal must pass the caller's separate admission/authority gate.
"""
import re
from dataclasses import dataclass

__all__ = ["Attempt", "Decision", "Task", "decide"]

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}\Z", re.ASCII)
_STATES = frozenset({"RUNNING", "CANDIDATE", "VERIFIED", "UNKNOWN", "FAILED", "CANCELLED"})
_ERROR = "CONDUCTOR_INPUT_INVALID"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Attempt:
    task_id: str
    attempt_id: str
    state: str


@dataclass(frozen=True, slots=True)
class Decision:
    kind: str
    task_id: str | None
    attempt_id: str | None
    reason: str


def _identity(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def decide(
    tasks: tuple[Task, ...],
    observations: tuple[Attempt, ...],
    *,
    attempts_used: int,
    max_attempts: int,
    max_running: int,
) -> Decision:
    """Recommend one next step from a complete trusted caller snapshot.

    No automatic retries. UNKNOWN requires owner reconciliation. VERIFIED is an
    external gate projection, never a completion certificate produced here.
    attempts_used includes historical attempts omitted from the current projection.
    """
    if not (
        type(tasks) is tuple and 0 < len(tasks) <= 256
        and type(observations) is tuple and len(observations) <= len(tasks)
        and type(attempts_used) is int and attempts_used >= len(observations)
        and type(max_attempts) is int and max_attempts > 0
        and type(max_running) is int and max_running > 0
    ):
        raise ValueError(_ERROR)
    by_id = {}
    for task in tasks:
        if not (
            type(task) is Task and _identity(task.task_id)
            and type(task.dependencies) is tuple and len(task.dependencies) <= 256
            and all(_identity(dep) for dep in task.dependencies)
            and len(set(task.dependencies)) == len(task.dependencies)
            and task.task_id not in by_id
        ):
            raise ValueError(_ERROR)
        by_id[task.task_id] = task
    for task in tasks:
        if any(dep not in by_id for dep in task.dependencies):
            raise ValueError(_ERROR)
    # Iterative DAG validation avoids recursion limits on caller-controlled input.
    visited = set()
    while len(visited) < len(tasks):
        ready = {t.task_id for t in tasks if set(t.dependencies) <= visited} - visited
        if not ready:
            raise ValueError(_ERROR)
        visited.update(ready)
    by_task = {}
    attempt_ids = set()
    for attempt in observations:
        if not (
            type(attempt) is Attempt and _identity(attempt.task_id)
            and _identity(attempt.attempt_id) and type(attempt.state) is str
            and attempt.state in _STATES and attempt.task_id in by_id
            and attempt.task_id not in by_task and attempt.attempt_id not in attempt_ids
        ):
            raise ValueError(_ERROR)
        by_task[attempt.task_id] = attempt
        attempt_ids.add(attempt.attempt_id)
    verified = {a.task_id for a in observations if a.state == "VERIFIED"}
    for attempt in observations:
        if not set(by_id[attempt.task_id].dependencies) <= verified:
            raise ValueError(_ERROR)
    # Order is policy, not a claim that observations are authentic or current.
    for state, kind, reason in (
        ("UNKNOWN", "reconcile_required", "unknown execution outcome; never blind retry"),
        ("FAILED", "halt", "failure requires a new owner-approved plan"),
        ("CANCELLED", "halt", "cancellation is not retry permission"),
        ("CANDIDATE", "verify_request", "executor claims are not verification"),
    ):
        for task in tasks:
            attempt = by_task.get(task.task_id)
            if attempt is not None and attempt.state == state:
                return Decision(kind, task.task_id, attempt.attempt_id, reason)
    if len(verified) == len(tasks):
        return Decision("accepted_projection", None, None, "all caller gate projections are VERIFIED")
    if sum(a.state == "RUNNING" for a in observations) >= max_running:
        return Decision("wait", None, None, "concurrency limit reached")
    ready_tasks = [
        t for t in tasks if t.task_id not in by_task and set(t.dependencies) <= verified
    ]
    if ready_tasks:
        if attempts_used >= max_attempts:
            return Decision("halt", None, None, "attempt budget exhausted")
        return Decision(
            "dispatch_proposal", ready_tasks[0].task_id, None,
            "requires separate authoritative admission before any runtime call",
        )
    return Decision("wait", None, None, "dependencies are not yet verified")
