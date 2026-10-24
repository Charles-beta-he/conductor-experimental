"""Real local Python chain. All mutation requires an explicit init/run command."""
import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

from acp_conductor_experiment import Task
from acp_conductor_runtime.contracts import Grant, Request
from acp_conductor_runtime.process import SubprocessAdapter
from acp_conductor_runtime.store import Store

from acp_conductor_composition import Binding, Flow


def adapter():
    return SubprocessAdapter((sys.executable, '-I', '-c',
                              'import json,sys;print(json.load(sys.stdin)["prompt"])'))


def initialize(root):
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    runtime = adapter()
    store = Store(root / 'attempts', max_starts=2, max_running=1)
    bindings = []
    for name, deps in [('first', ()), ('second', ('first',))]:
        request = Request(name, runtime.identity, name.upper(), name.upper(), timeout=3)
        grant = Grant(request.digest, runtime.identity, time.time() + 300)
        attempt_id = store.submit(name, request, grant)
        bindings.append({'task': asdict(Task(name, deps)), 'request': asdict(request),
                         'attempt_id': attempt_id})
    fd = os.open(root / 'flow.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'version': 1, 'bindings': bindings}, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def load(root):
    data = json.loads((root / 'flow.json').read_text())
    if set(data) != {'version', 'bindings'} or data['version'] != 1:
        raise ValueError('invalid host manifest')
    bindings = tuple(Binding(Task(b['task']['task_id'], tuple(b['task']['dependencies'])),
                             Request(**b['request']), b['attempt_id']) for b in data['bindings'])
    runtime = adapter()
    store = Store(root / 'attempts', max_starts=2, max_running=1)
    return store, bindings, {runtime.identity: runtime}


def snapshot(store, bindings):
    rows = [store.query(b.attempt_id) for b in bindings]
    with sqlite3.connect(f'{store.db.as_uri()}?mode=ro', uri=True) as conn:
        counts = dict(conn.execute("SELECT attempt_id,count(*) FROM events "
                                   "WHERE state='RUNNING' GROUP BY attempt_id"))
    return [{'task': row['task_id'], 'attempt': row['attempt_id'], 'state': row['state'],
             'stored_state': row.get('stored_state', row['state']),
             'started': row['started'], 'running_events': counts.get(row['attempt_id'], 0)}
            for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'run', 'read'])
    parser.add_argument('root', type=Path)
    parser.add_argument('--ticks', type=int, default=1)
    args = parser.parse_args()
    root = args.root.absolute()
    if args.command == 'init':
        initialize(root)
    store, bindings, adapters = load(root)
    report = Flow(store, bindings, adapters).drive(max_ticks=args.ticks) \
        if args.command == 'run' else None
    print(json.dumps({'status': report.status if report else 'readback',
                      'ticks': len(report.ticks) if report else 0,
                      'snapshot': snapshot(store, bindings)}, sort_keys=True))


if __name__ == '__main__':
    main()
