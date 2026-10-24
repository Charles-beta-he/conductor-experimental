# Plan-admission journal — experimental, HOLD

This separately packaged draft owns local plan metadata only. It does not own
runtime attempts or ACP lifecycle. A plan Approval is not an execution Grant.
The journal imports only the standalone proposal-only planner and Python stdlib.

## Implemented draft boundaries

- Immutable persisted initial plan and caps; proposal reservation permanently
  spends a round before any host callback. Existing reservation keys never
  authorize replay. Explicit proposal-round UNKNOWN closure does not refund.
- Bounded strict JSON proposal envelopes: version 1 and tasks; each task has
  task_id and dependencies only. Proposal data cannot select code or grant work.
- Exact base/candidate digest plan approval and independent revision cap.
- Immutable historical task/attempt bindings; complete host-supplied fresh
  observations, including retired tasks, are required for adoption.
- UNKNOWN/RUNNING history blocks adoption. Execution checks and exact runtime
  Grants remain a separate trusted-host responsibility.

## Known blocking acceptance conflict

Design R1 review found that the frozen runtime has NO public transition to
FAILED. Actual execution errors and failed output verification become UNKNOWN.
There is no public UNKNOWN-resolution protocol. Therefore the required positive
failed-execution -> successful replan path in SPEC.md cannot currently pass.

`demo.py` is an UNACCEPTED draft: its init stage incorrectly asserts FAILED after
an output mismatch, whereas the actual frozen runtime records UNKNOWN. Do not
use the demo as a working recovery example. Unit tests that supply a synthetic
FAILED host observation test journal policy only; they do not establish a
reachable state in the current runtime or an end-to-end recovery capability.

No frozen runtime changes, forged FAILED projection, dropping UNKNOWN history,
or private state setter may be used to work around this blocker. The requirement
choice needs an explicit human decision. SPEC.md remains unchanged; no cancelled
or verified-history alternative is silently substituted for failed recovery.

Cross-store automatic recovery, live LLM planning, cross-runtime replanning,
platform lifecycle acceptance, and production readiness remain unverified.

## Verification scope

Run draft tests with planner and journal source roots on PYTHONPATH. Tests cover
journal policy, SQLite races, and a real killed proposal-reservation process.
Wheel isolation/byte-identity checks, if provided in private evidence, establish
packaging only and do not lift the design HOLD or certify the demo.

New experimental code uses the repository's Apache-2.0 license. No private
Conductor implementation or external framework code was copied into this draft.
No public release or license-clearance decision is implied.
