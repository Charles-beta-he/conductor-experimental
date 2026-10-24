# Fixed-DAG conductor composition — M3 local experiment

This separately packaged synchronous layer composes `acp-conductor-experiment`
(planning only) and `acp-conductor-runtime-experiment` (durable local attempts).
It does not modify either dependency or install anything into active Hermes/ACP.

The trusted host pre-submits every task under an explicit Grant, then supplies:

```python
flow = Flow(store, (
    Binding(Task('first'), first_request, first_attempt_id),
    Binding(Task('second', ('first',)), second_request, second_attempt_id),
), {adapter.identity: adapter})
report = flow.drive(max_ticks=5)
```

Every tick validates the entire binding set against fresh Store queries. A ready
PREPARED task can run only through Store.run (including M2 claim and expiry gates).
A CANDIDATE needs a separate verification tick; only a fresh VERIFIED projection
satisfies dependencies. UNKNOWN stops for reconciliation without auto-replay;
CANCELLED/FAILED halt; RUNNING waits rather than being declared dead.

`Tick.decision` is the pre-action planner recommendation; `Tick.state` is the
returned post-action observation when an action happened (or a terminal state).
A dispatch_proposal tick can therefore return UNKNOWN without implying a success.
`Report.status` exposes that as reconcile_required. No report is certification.

`max_ticks` is a strict per-invocation cap (1..1024), not a durable lifetime decision
budget or wall-clock deadline. The durable global start/concurrency reservations
are still M2-owned. Fixed-plan maximum attempts equals task count; PREPARED
reservations are omitted from planner observations and have not consumed a start.
UNKNOWN/terminal prechecks preserve planner validity, including unstarted CANCELLED.
No automatic submit, grant creation, reconcile, cancellation, retries or replan.

Flow serializes actions only within one synchronous caller. The host supplies a
complete immutable DAG, stable attempt IDs and trusted adapter objects. Bindings
are not a durable/authenticated plan SSOT. Query snapshots and dispatch are not a
cross-task transaction, hostile-writer fence or concurrent mutable-plan scheduler.
M2 claim fencing still prevents duplicate starts for the same attempt. A fixed
chain proves scheduling dependencies, NOT dynamic artifact-to-prompt dataflow.

## Real Python demo (macOS/Linux, Python >=3.11)

Install all three local wheels explicitly (not from an unrelated package index).
With their packages installed, choose a NEW path whose parent already exists:

```text
python demo.py init /your/private/new-flow
python demo.py run /your/private/new-flow --ticks 1
python demo.py run /your/private/new-flow --ticks 4
python demo.py read /your/private/new-flow
```

Each command is a fresh process. `init` creates private attempts and an exclusive
host manifest, without execution. `run --ticks 1` returns tick_budget_exhausted
with first=CANDIDATE and second=PREPARED. Next invocation verifies first, starts and
verifies second, then returns accepted_projection. `read` only queries existing
attempts (opening Store may check/create schema; not a pure filesystem read tool).
Manifest adapter identity binds the demo interpreter path; switching interpreters
or adapter code refuses rather than silently using another runtime. Grants last
300 seconds: expired preparation halts and does not renew itself. Demo manifest
is private host data, not shipped data or an authoritative platform workflow.

## Verification / limitations

Run this package's tests from this directory; run r1/M2 suites from their own roots
so their distinct `demo.py` imports do not collide. Tests deliberately modify only
new temporary stores for state/fault injection. No test or demo needs credentials,
network or a provider. Source test paths in pyproject are development conveniences;
clean installed-package execution must separately prove actual wheel imports.

M2 trusted local operator/filesystem/adapter assumptions, external Hermes revision
admission, fixed pin-roster limitations and missing live tool-denial negative remain
unchanged. This package supplies no Hermes runtime, ACP lifecycle, remote auth,
OS sandbox, automatic replan, durable plan owner, outbox or production activation.
Full conductor product acceptance and publication remain separate future scopes.
See SPEC.md for the bounded M3 acceptance contract.
