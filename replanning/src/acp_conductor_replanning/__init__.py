# SPDX-License-Identifier: Apache-2.0
"""Durable local plan metadata; never executes or owns attempt lifecycle.

The trusted host supplies complete fresh historical observations. Plan approval
is not a runtime Grant. A reservation does not authorize any model/tool call.
"""

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from acp_conductor_experiment import Task, decide

__all__ = ["Approval", "Journal", "Observation"]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}\Z", re.ASCII)
_STATES = {"PREPARED", "RUNNING", "CANDIDATE", "VERIFIED", "UNKNOWN", "FAILED", "CANCELLED"}


def _identity(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("invalid identity")
    return value


def _encode(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value):
    return hashlib.sha256(_encode(value).encode("ascii")).hexdigest()


def _tasks(tasks):
    decide(tasks, (), attempts_used=0, max_attempts=1, max_running=1)
    return [{"task_id": t.task_id, "dependencies": list(t.dependencies)} for t in tasks]


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _proposal(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("invalid proposal bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
        if (
            type(value) is not dict
            or set(value) != {"version", "tasks"}
            or type(value["version"]) is not int
            or value["version"] != 1
        ):
            raise ValueError("invalid proposal envelope")
        rows = value["tasks"]
        if type(rows) is not list or not 0 < len(rows) <= 256:
            raise ValueError("invalid task list")
        tasks = []
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != {"task_id", "dependencies"}
                or type(row["dependencies"]) is not list
            ):
                raise ValueError("invalid task fields")
            tasks.append(Task(row["task_id"], tuple(row["dependencies"])))
        return _tasks(tuple(tasks))
    except (UnicodeError, RecursionError, TypeError, KeyError) as exc:
        raise ValueError("invalid proposal") from exc


@dataclass(frozen=True, slots=True)
class Approval:
    base_digest: str
    candidate_digest: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class Observation:
    task_id: str
    attempt_id: str
    state: str


class Journal:
    """Serial SQLite plan-admission metadata, not a worker or attempt store.

    Reopen with identical original initial plan/caps. Each operation uses its own
    connection/transaction; no connection is shared across process boundaries.
    Unknown cross-store execution status must be reconciled by the host, not here.
    """

    def __init__(self, path, *, initial, max_rounds, max_revisions):
        if any(type(v) is not int or not 0 < v <= 1024 for v in (max_rounds, max_revisions)):
            raise ValueError("invalid finite caps")
        rows = _tasks(initial)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "initial_digest": _digest(rows),
            "max_rounds": max_rounds,
            "max_revisions": max_revisions,
        }
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plan_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT body FROM plan_metadata WHERE singleton=1").fetchone()
            if existing is None:
                state = {
                    "version": 1,
                    "config": config,
                    "plans": [rows],
                    "rounds": {},
                    "bindings": {},
                    "identities": {r["task_id"]: r["dependencies"] for r in rows},
                    "adoptions": {},
                }
                db.execute("INSERT INTO plan_metadata VALUES (1, ?)", (_encode(state),))
            elif json.loads(existing[0])["config"] != config:
                raise ValueError("journal configuration cannot change")

    @contextmanager
    def _transaction(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM plan_metadata WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("missing plan metadata")
            state = json.loads(row[0])
            if state["version"] != 1:
                raise ValueError("unsupported journal")
            yield state
            db.execute("UPDATE plan_metadata SET body=? WHERE singleton=1", (_encode(state),))

    @staticmethod
    def _snapshot(state):
        rows = state["plans"][-1]
        # The admission digest includes its revision, preventing plan ABA reuse.
        revision = len(state["plans"]) - 1
        return {
            "revision": revision,
            "digest": _digest({"revision": revision, "tasks": rows}),
            "tasks": rows,
            "rounds_used": len(state["rounds"]),
            "bindings": dict(state["bindings"]),
        }

    def snapshot(self):
        with sqlite3.connect(f"{self.path.absolute().as_uri()}?mode=ro", uri=True) as db:
            row = db.execute("SELECT body FROM plan_metadata WHERE singleton=1").fetchone()
            if row is None:
                raise ValueError("missing plan metadata")
            return self._snapshot(json.loads(row[0]))

    def reserve(self, key):
        _identity(key)
        with self._transaction() as state:
            rounds = state["rounds"]
            if key in rounds:
                return {**rounds[key], "created": False}
            if any(r["state"] == "CLAIMED" for r in rounds.values()):
                raise ValueError("unresolved proposal round; reconcile, never replay")
            if len(rounds) >= state["config"]["max_rounds"]:
                raise ValueError("cumulative round budget exhausted")
            record = {
                "round_id": key,
                "state": "CLAIMED",
                "base_digest": self._snapshot(state)["digest"],
            }
            rounds[key] = record
            return {**record, "created": True}

    def close_unknown(self, key):
        _identity(key)
        with self._transaction() as state:
            record = state["rounds"].get(key)
            if record is None or record["state"] not in {"CLAIMED", "UNKNOWN"}:
                raise ValueError("not an unresolved proposal reservation")
            record["state"] = "UNKNOWN"

    def offer(self, key, raw):
        _identity(key)
        error = None
        with self._transaction() as state:
            record = state["rounds"].get(key)
            if record is None or record["state"] not in {"CLAIMED", "PROPOSED"}:
                raise ValueError("no matching proposal reservation")
            if record["state"] == "PROPOSED":
                if (
                    type(raw) is not bytes
                    or hashlib.sha256(raw).hexdigest() != record["raw_digest"]
                ):
                    raise ValueError("proposal identity conflict")
            else:
                try:
                    rows = _proposal(raw)
                except ValueError as exc:
                    record["state"] = "INVALID"
                    error = exc
                else:
                    record.update(
                        state="PROPOSED",
                        tasks=rows,
                        candidate_digest=_digest(rows),
                        raw_digest=hashlib.sha256(raw).hexdigest(),
                    )
            result = dict(record)
        # Commit the failed proposal's spent reservation before raising.
        if error is not None:
            raise error
        return result

    def bind(self, task_id, attempt_id):
        _identity(task_id)
        _identity(attempt_id)
        with self._transaction() as state:
            if task_id not in {r["task_id"] for r in state["plans"][-1]}:
                raise ValueError("task not in current plan")
            bindings = state["bindings"]
            if task_id in bindings:
                if bindings[task_id] != attempt_id:
                    raise ValueError("attempt rebinding prohibited")
                return
            if attempt_id in bindings.values():
                raise ValueError("attempt already bound to different task")
            bindings[task_id] = attempt_id

    @staticmethod
    def _observations(state, observations, rows):
        bindings = state["bindings"]
        if type(observations) is not tuple or len(observations) != len(bindings):
            raise ValueError("incomplete historical observations")
        observed = {}
        for item in observations:
            if (
                type(item) is not Observation
                or item.task_id in observed
                or type(item.state) is not str
                or item.state not in _STATES
                or bindings.get(item.task_id) != item.attempt_id
            ):
                raise ValueError("invalid historical observation")
            observed[item.task_id] = item.state
        if set(observed) != set(bindings):
            raise ValueError("incomplete history")
        if any(v in {"UNKNOWN", "RUNNING"} for v in observed.values()):
            raise ValueError("historical attempt uncertain or running")
        present = {r["task_id"] for r in rows}
        if any(v in {"PREPARED", "CANDIDATE"} and k not in present for k, v in observed.items()):
            raise ValueError("unfinished attempt cannot be dropped")
        for row in rows:
            old = state["identities"].get(row["task_id"])
            if old is not None and old != row["dependencies"]:
                raise ValueError("task identity cannot change dependencies")

    def adopt(self, key, round_id, approval, observations, *, now):
        _identity(key)
        _identity(round_id)
        if type(approval) is not Approval:
            raise PermissionError("explicit plan approval required")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) for v in (now, approval.expires_at)
        ):
            raise ValueError("finite trusted host time required")
        with self._transaction() as state:
            candidate = state["rounds"].get(round_id)
            if candidate is None or candidate["state"] != "PROPOSED":
                raise ValueError("no proposed plan")
            identity = [round_id, approval.base_digest, approval.candidate_digest]
            previous = state["adoptions"].get(key)
            if previous is not None:
                if previous["identity"] != identity:
                    raise ValueError("adoption identity conflict")
                return previous["result"]
            if any(r["state"] == "CLAIMED" for r in state["rounds"].values()):
                raise ValueError("unresolved proposal round")
            current = self._snapshot(state)
            if (
                approval.base_digest != current["digest"]
                or candidate["base_digest"] != current["digest"]
                or approval.candidate_digest != candidate["candidate_digest"]
                or now >= approval.expires_at
            ):
                raise PermissionError("stale, mismatched or expired plan approval")
            if current["revision"] >= state["config"]["max_revisions"]:
                raise ValueError("cumulative revision budget exhausted")
            rows = candidate["tasks"]
            self._observations(state, observations, rows)
            state["plans"].append(rows)
            state["identities"].update({r["task_id"]: r["dependencies"] for r in rows})
            result = self._snapshot(state)
            state["adoptions"][key] = {"identity": identity, "result": result}
            return result
