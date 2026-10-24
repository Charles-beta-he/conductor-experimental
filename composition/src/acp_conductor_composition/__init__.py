# SPDX-License-Identifier: Apache-2.0
"""Synchronous fixed-plan composition; no grant or lifecycle ownership."""
from dataclasses import dataclass

from acp_conductor_experiment import Attempt, Decision, Task, decide
from acp_conductor_runtime.contracts import Request, identity
from acp_conductor_runtime.store import Store

__all__ = ['Binding', 'Flow', 'Report', 'Tick']


@dataclass(frozen=True, slots=True)
class Binding:
    task: Task
    request: Request
    attempt_id: str


@dataclass(frozen=True, slots=True)
class Tick:
    decision: Decision
    state: str | None = None


@dataclass(frozen=True, slots=True)
class Report:
    status: str
    ticks: tuple[Tick, ...]


class Flow:
    """Host binds pre-submitted attempts; M2 remains the only execution gate.

    Budgets on drive are per invocation, not lifetime counters. Bindings are
    caller-owned, not a durable authenticated plan. No automatic retries/replan.
    """

    def __init__(self, store: Store, bindings: tuple[Binding, ...], adapters: dict):
        if (type(store) is not Store or type(bindings) is not tuple
                or not 0 < len(bindings) <= 256 or type(adapters) is not dict):
            raise ValueError('invalid flow inputs')
        attempts = set()
        for binding in bindings:
            if (type(binding) is not Binding or type(binding.task) is not Task
                    or type(binding.request) is not Request
                    or binding.task.task_id != binding.request.task_id
                    or not identity(binding.attempt_id) or binding.attempt_id in attempts):
                raise ValueError('invalid or duplicate binding')
            attempts.add(binding.attempt_id)
        tasks = tuple(b.task for b in bindings)
        # Reuse canonical planner validation rather than a second DAG algorithm.
        decide(tasks, (), attempts_used=0, max_attempts=len(tasks), max_running=1)
        self._store = store
        self._bindings = bindings
        self._adapters = dict(adapters)
        self._tasks = tasks

    def _preflight(self):
        rows = []
        for binding in self._bindings:
            adapter = self._adapters.get(binding.request.adapter_id)
            if (getattr(adapter, 'identity', None) != binding.request.adapter_id
                    or not (callable(getattr(adapter, 'execute', None))
                            or callable(getattr(adapter, 'execute_authorized', None)))):
                raise ValueError('missing or mismatched trusted adapter')
            row = self._store.query(binding.attempt_id)
            if (row['attempt_id'] != binding.attempt_id
                    or row['task_id'] != binding.task.task_id
                    or row['adapter_id'] != binding.request.adapter_id
                    or row['digest'] != binding.request.digest
                    or row['request'] != binding.request.encoded
                    or row['state'] not in {'PREPARED', 'RUNNING', 'CANDIDATE', 'VERIFIED',
                                            'UNKNOWN', 'FAILED', 'CANCELLED'}
                    or type(row['started']) is not int or row['started'] not in (0, 1)):
                raise ValueError('attempt does not match complete host binding')
            rows.append(row)
        return tuple(rows)

    def tick(self) -> Tick:
        rows = self._preflight()
        # A revoked dependency can invalidate an already-started descendant's
        # planner snapshot. UNKNOWN must stop before strict snapshot validation.
        for row in rows:
            if row['state'] == 'UNKNOWN':
                return Tick(Decision('reconcile_required', row['task_id'], row['attempt_id'],
                                     'fresh store projection UNKNOWN; never replay'), 'UNKNOWN')
        for row in rows:
            if row['state'] in {'CANCELLED', 'FAILED'}:
                return Tick(Decision('halt', row['task_id'], row['attempt_id'],
                                     'terminal attempt; no retry permission'), row['state'])
        observations = tuple(Attempt(r['task_id'], r['attempt_id'], r['state'])
                             for r in rows if r['state'] != 'PREPARED')
        decision = decide(self._tasks, observations,
                          attempts_used=sum(r['started'] for r in rows),
                          max_attempts=len(self._bindings), max_running=1)
        if decision.kind == 'verify_request':
            row = self._store.verify(decision.attempt_id)
            return Tick(decision, row['state'])
        if decision.kind == 'dispatch_proposal':
            index = next(i for i, b in enumerate(self._bindings)
                         if b.task.task_id == decision.task_id)
            row = rows[index]
            if row['state'] != 'PREPARED':
                raise ValueError('planner dispatch is not PREPARED')
            try:
                result = self._store.run(row['attempt_id'], self._adapters[row['adapter_id']])
            except (PermissionError, ValueError) as exc:
                # M2 rejected before claim/dispatch (e.g. grant or global cap).
                return Tick(Decision('halt', row['task_id'], row['attempt_id'],
                                     f'M2 gate refused: {type(exc).__name__}'))
            return Tick(decision, result['state'])
        return Tick(decision)

    def drive(self, *, max_ticks: int) -> Report:
        if type(max_ticks) is not int or not 1 <= max_ticks <= 1024:
            raise ValueError('max_ticks must be a strict integer in 1..1024')
        ticks = []
        for _ in range(max_ticks):
            tick = self.tick()
            ticks.append(tick)
            if tick.state == 'UNKNOWN':
                return Report('reconcile_required', tuple(ticks))
            if tick.decision.kind in {'accepted_projection', 'reconcile_required', 'halt', 'wait'}:
                return Report(tick.decision.kind, tuple(ticks))
        return Report('tick_budget_exhausted', tuple(ticks))
