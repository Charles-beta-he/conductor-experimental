import json
import sqlite3
import sys
import time
from dataclasses import replace

import pytest
from acp_conductor_experiment import Task
from acp_conductor_runtime.contracts import Grant, Request
from acp_conductor_runtime.process import SubprocessAdapter
from acp_conductor_runtime.store import Store

from acp_conductor_composition import Binding, Flow


@pytest.fixture
def chain(tmp_path):
    store = Store(tmp_path / 'store', max_starts=2, max_running=1)
    adapter = SubprocessAdapter((sys.executable, '-I', '-c',
                                'import json,sys;print(json.load(sys.stdin)["prompt"])'))
    bindings = []
    for name, deps in [('first', ()), ('second', ('first',))]:
        request = Request(name, adapter.identity, name.upper(), name.upper(), timeout=3)
        attempt = store.submit(name, request, Grant(request.digest, adapter.identity, time.time()+60))
        bindings.append(Binding(Task(name, deps), request, attempt))
    return store, tuple(bindings), {adapter.identity: adapter}


def make_flow(chain):
    return Flow(*chain)


def starts(store):
    with sqlite3.connect(store.db) as conn:
        return dict(conn.execute('SELECT task_id,started FROM attempts'))


def events(store):
    with sqlite3.connect(store.db) as conn:
        return list(conn.execute('SELECT attempt_id,state FROM events ORDER BY seq'))


def test_real_chain_separate_candidate_verification(chain):
    store, bindings, _ = chain
    flow = make_flow(chain)
    assert flow.tick().decision.kind == 'dispatch_proposal'
    assert store.query(bindings[0].attempt_id)['state'] == 'CANDIDATE'
    assert starts(store) == {'first': 1, 'second': 0}
    assert flow.tick().decision.kind == 'verify_request'
    assert store.query(bindings[0].attempt_id)['state'] == 'VERIFIED'
    assert starts(store)['second'] == 0
    assert flow.tick().decision.task_id == 'second'
    assert flow.tick().decision.kind == 'verify_request'
    assert flow.tick().decision.kind == 'accepted_projection'
    before = events(store)
    assert flow.drive(max_ticks=5).status == 'accepted_projection'
    assert events(store) == before
    assert all(store.artifact(b.attempt_id).strip() == b.request.expected.encode() for b in bindings)


def test_cap_and_reopen(chain):
    store, bindings, adapters = chain
    report = make_flow(chain).drive(max_ticks=1)
    assert report.status == 'tick_budget_exhausted'
    assert len(report.ticks) == 1
    reopened = Store(store.root, max_starts=2, max_running=1)
    report = Flow(reopened, bindings, adapters).drive(max_ticks=4)
    assert report.status == 'accepted_projection'
    assert starts(store) == {'first': 1, 'second': 1}
    assert [s for _, s in events(store)].count('RUNNING') == 2


@pytest.mark.parametrize('cap', [True, False, 0, -1, 1025, 1.0, '2', None])
def test_invalid_caps_before_work(chain, cap):
    with pytest.raises(ValueError):
        make_flow(chain).drive(max_ticks=cap)
    assert starts(chain[0]) == {'first': 0, 'second': 0}


def test_running_is_wait_not_reconcile(chain):
    store, bindings, _ = chain
    # Synthetic claim models an external still-running caller; Flow must not adopt it.
    with sqlite3.connect(store.db) as conn:
        conn.execute("UPDATE attempts SET state='RUNNING',started=1 WHERE attempt_id=?",
                     (bindings[0].attempt_id,))
    before = events(store)
    assert make_flow(chain).drive(max_ticks=4).status == 'wait'
    assert store.query(bindings[0].attempt_id)['state'] == 'RUNNING'
    assert starts(store)['second'] == 0
    assert events(store) == before


def test_unknown_preempts_and_no_replay(chain):
    store, bindings, _ = chain
    with sqlite3.connect(store.db) as conn:
        conn.execute("UPDATE attempts SET state='UNKNOWN',started=1 WHERE attempt_id=?",
                     (bindings[0].attempt_id,))
    before = events(store)
    for _ in range(2):
        assert make_flow(chain).drive(max_ticks=8).status == 'reconcile_required'
    assert events(store) == before
    assert starts(store) == {'first': 1, 'second': 0}


def test_cancelled_halts(chain):
    store, bindings, _ = chain
    store.cancel(bindings[0].attempt_id)
    assert make_flow(chain).drive(max_ticks=8).status == 'halt'
    assert starts(store) == {'first': 0, 'second': 0}


@pytest.mark.parametrize('damage', ['corrupt', 'missing', 'fifo'])
def test_invalid_verified_upstream_stops_downstream(chain, damage):
    import os
    from pathlib import Path
    store, bindings, _ = chain
    flow = make_flow(chain)
    flow.drive(max_ticks=2)
    row = store.query(bindings[0].attempt_id)
    path = Path(row['artifact_path'])
    path.unlink()
    if damage == 'corrupt':
        path.write_bytes(b'wrong')
    elif damage == 'fifo':
        os.mkfifo(path)
    assert flow.drive(max_ticks=10).status == 'reconcile_required'
    assert starts(store)['second'] == 0
    assert store.query(bindings[0].attempt_id)['stored_state'] == 'VERIFIED'


def test_corrupt_candidate_rejected_not_downstream(chain):
    from pathlib import Path
    store, bindings, _ = chain
    flow = make_flow(chain)
    flow.tick()
    Path(store.query(bindings[0].attempt_id)['artifact_path']).write_bytes(b'wrong')
    assert flow.drive(max_ticks=8).status == 'reconcile_required'
    assert store.query(bindings[0].attempt_id)['state'] == 'UNKNOWN'
    assert starts(store)['second'] == 0


def test_changed_dependency_after_downstream_started_conservative(chain):
    from pathlib import Path
    store, bindings, _ = chain
    flow = make_flow(chain)
    flow.drive(max_ticks=3)
    Path(store.query(bindings[0].attempt_id)['artifact_path']).unlink()
    before = events(store)
    assert flow.tick().decision.kind == 'reconcile_required'
    assert events(store) == before
    assert store.query(bindings[1].attempt_id)['state'] == 'CANDIDATE'


@pytest.mark.parametrize('bad', ['request', 'attempt', 'adapter', 'missing-adapter'])
def test_whole_set_bad_downstream_rejected_before_first_run(chain, bad):
    store, bindings, adapters = chain
    downstream = bindings[1]
    if bad == 'request':
        downstream = replace(downstream, request=replace(downstream.request, prompt='wrong'))
    elif bad == 'attempt':
        downstream = replace(downstream, attempt_id='not-present')
    elif bad == 'adapter':
        adapters = {next(iter(adapters)): object()}
    else:
        adapters = {}
    with pytest.raises((ValueError, KeyError)):
        Flow(store, (bindings[0], downstream), adapters).tick()
    assert starts(store) == {'first': 0, 'second': 0}


def test_cross_attempt_binding_refused(chain):
    store, bindings, adapters = chain
    altered = replace(bindings[1], attempt_id=bindings[0].attempt_id)
    with pytest.raises(ValueError):
        Flow(store, (bindings[0], altered), adapters).tick()
    assert starts(store) == {'first': 0, 'second': 0}


@pytest.mark.parametrize('bad', ['cycle', 'unknown-dep', 'duplicate-task', 'task-request', 'list'])
def test_invalid_plan(chain, bad):
    store, bindings, adapters = chain
    if bad == 'cycle':
        bindings = (replace(bindings[0], task=Task('first', ('second',))), bindings[1])
    elif bad == 'unknown-dep':
        bindings = (bindings[0], replace(bindings[1], task=Task('second', ('missing',))))
    elif bad == 'duplicate-task':
        bindings = (bindings[0], bindings[0])
    elif bad == 'task-request':
        bindings = (replace(bindings[0], task=Task('other')), bindings[1])
    else:
        bindings = list(bindings)
    with pytest.raises(ValueError):
        Flow(store, bindings, adapters).tick()
    assert starts(store) == {'first': 0, 'second': 0}


def test_expected_text_mismatch_never_unlocks_dependency(tmp_path, chain):
    adapter = next(iter(chain[2].values()))
    store = Store(tmp_path / 'wrong-answer', max_starts=2, max_running=1)
    bindings = []
    for name, deps in [('first', ()), ('second', ('first',))]:
        request = Request(name, adapter.identity, name.upper(),
                          'WRONG' if name == 'first' else name.upper())
        attempt = store.submit(name, request,
                               Grant(request.digest, adapter.identity, time.time() + 60))
        bindings.append(Binding(Task(name, deps), request, attempt))
    report = Flow(store, tuple(bindings), {adapter.identity: adapter}).drive(max_ticks=10)
    assert report.status == 'reconcile_required'
    assert store.query(bindings[0].attempt_id)['state'] == 'UNKNOWN'
    assert store.query(bindings[1].attempt_id)['started'] == 0
    assert starts(store) == {'first': 1, 'second': 0}


def test_expired_grant_halts_no_execution(chain):
    store, _bindings, _ = chain
    with sqlite3.connect(store.db) as conn:
        conn.execute('UPDATE attempts SET expires=0')
    assert make_flow(chain).drive(max_ticks=10).status == 'halt'
    assert starts(store) == {'first': 0, 'second': 0}


def test_durable_budget_not_reset_by_new_flow(chain):
    store, bindings, _ = chain
    with sqlite3.connect(store.db) as conn:
        conn.execute('UPDATE config SET max_starts=1')
    flow = make_flow(chain)
    assert flow.drive(max_ticks=8).status == 'halt'
    reopened = Store(store.root, max_starts=1, max_running=1)
    assert Flow(reopened, bindings, chain[2]).drive(max_ticks=8).status == 'halt'
    assert starts(store) == {'first': 1, 'second': 0}


def test_no_submit_or_reconcile_hidden(chain, monkeypatch):
    store, _, _ = chain
    def denied(*args, **kwargs):
        raise AssertionError('Flow must not submit/reconcile/cancel')
    for method in ['submit', 'reconcile', 'cancel']:
        monkeypatch.setattr(store, method, denied)
    assert make_flow(chain).drive(max_ticks=5).status == 'accepted_projection'


def test_binding_has_no_caller_state(chain):
    binding = chain[1][0]
    assert not hasattr(binding, 'state')
    assert json.loads(binding.request.encoded)['task_id'] == binding.task.task_id
