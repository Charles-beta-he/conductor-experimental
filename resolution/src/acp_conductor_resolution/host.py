# SPDX-License-Identifier: Apache-2.0
"""Synchronous trusted v2 host, distinct from frozen v1 Flow/M4 dataflow.

All cooperating host writers use this journal-scoped lock. Direct store mutation
outside the host is an explicit trusted-owner operation, not concurrent remote
consistency. No cross-database transaction, sandbox or automatic retry is claimed.
"""

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

from acp_conductor_experiment import Attempt, Task, decide
from acp_conductor_replanning import Journal, Observation
from acp_conductor_runtime.contracts import Request

from .store import Store


class Host:
    def __init__(self, store, journal):
        if type(store) is not Store or type(journal) is not Journal:
            raise ValueError("exact public v2 Store and Journal required")
        self.store, self.journal = store, journal
        self.lock = Path(str(journal.path) + ".v2-host.lock")

    @contextmanager
    def _locked(self):
        with self.lock.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("host busy; no concurrent operation") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _history(self):
        snapshot = self.journal.snapshot()
        bindings = snapshot["bindings"]
        if set(self.store.inventory()) != set(bindings.values()):
            raise ValueError("unbound or missing attempt; explicit owner readback required")
        observations = []
        for task, attempt in sorted(bindings.items()):
            row = self.store.verify(attempt)
            request = Request(**json.loads(row["request"]))
            if request.task_id != task or request.digest != row["digest"] or row["id"] != attempt:
                raise ValueError("historical attempt identity mismatch")
            if row["state"] in {"UNKNOWN", "RUNNING"}:
                raise ValueError("historical attempt uncertain or running")
            observations.append(Observation(task, attempt, row["state"]))
        return snapshot, tuple(observations)

    def adopt(self, key, round_id, approval):
        with self._locked():
            _, observations = self._history()
            return self.journal.adopt(key, round_id, approval, observations, now=time.time())

    def step(self, attempt=None, request=None, grant=None, adapter=None):
        with self._locked():
            snapshot, history = self._history()
            tasks = tuple(
                Task(row["task_id"], tuple(row["dependencies"])) for row in snapshot["tasks"]
            )
            current = {task.task_id for task in tasks}
            observations = tuple(
                Attempt(o.task_id, o.attempt_id, o.state)
                for o in history
                if o.task_id in current and o.state != "PREPARED"
            )
            recommendation = decide(
                tasks,
                observations,
                attempts_used=self.store.stats()["starts"],
                max_attempts=self.store.max_starts,
                max_running=self.store.max_running,
            )
            if recommendation.kind != "dispatch_proposal":
                return recommendation
            if type(request) is not Request or request.task_id != recommendation.task_id:
                raise ValueError("request must match admitted dispatch proposal")
            bound = snapshot["bindings"].get(request.task_id)
            if bound is not None and bound != attempt:
                raise ValueError("attempt already bound")
            # If interrupted between these public operations, next call detects the
            # unbound inventory and stops. It never reconstructs or replays execution.
            self.store.submit(attempt, request, grant)
            self.journal.bind(request.task_id, attempt)
            self._history()  # all current AND retired owner state at the use point
            self.store.run(attempt, adapter)
            return self.store.verify(attempt)
