# Conductor extraction experiment v1 — canonical Spec Brief

Status: local experiment; not integrated, not published, not product-complete.

## Objective and success criteria

Long-term user goal: a small runtime-independent task coordination and evidence-acceptance layer, with Hermes as the first real Agent runtime adapter. This experiment is the first implementation milestone, NOT a replacement definition of that goal.

Current milestone: install a standalone, dependency-free scheduling-policy package; consume caller-owned snapshots; produce bounded next-step recommendations; compose existing ACP local-file public APIs into an actual disposable artifact/readback demonstration. Verify negative and restart cases, package isolation, and independent review.

## Facts and chosen path

- ACP G0 source baseline: bfe57f2f4eb1a9184cae06a2628a1a3e4b0fe35e. Existing local-file-effect and local-file-verifier packages already separate applying bytes, observations, receipts and verification requests. The durable-run-engine is initially noop; its name does not prove a live persistent lifecycle owner.
- Frozen OpenPersonalAgentPlatformTarget.v1 owns lifecycle and authority outside planners. Preserve it unchanged.
- Existing ACP worktrees contain unrelated changes. Work only in the new experimental worktree. No edits to their working files or active Hermes runtime.
- Do not copy private Conductor implementation. Reimplement only generic coordination concepts. Source provenance inventory is private audit material, not public package content.
- External frameworks are conceptual reference candidates only; no vendored code, runtime dependency or source-equivalence claim.

Paths considered: (a) duplicate the private ledger in a new project, rejected because it duplicates ACP ownership; (b) put execution into the advisory ACP core, rejected because it violates frozen boundaries; (c) standalone experimental pure planner plus a separate composition harness over existing public leaf APIs, chosen.

## Rules and boundaries

- Plan is a DAG of task identities and dependency identities. Snapshot is a complete caller-supplied projection, not an event log and not proof of execution.
- One task snapshot binds one attempt identity. The planner rejects duplicate task or attempt identities, unknown tasks/dependencies, cycles, invalid types and contradictory fields.
- Recommendations: dispatch_proposal, wait, verify_request, reconcile_required, halt, accepted_projection. None grants authority, dispatches work, writes lifecycle or certifies evidence.
- UNKNOWN has precedence and requires reconciliation. FAILED/CANCELLED halt; no automatic retry or side-effect replay. Candidate results request verification, never acceptance. Only a caller-owned VERIFIED projection satisfies dependencies. The planner does not authenticate that projection; callers must read it from their trusted gate.
- Attempts-used is an absolute caller-owned count, not a planner counter. All supplied attempts count against the budget; exhausted budget forbids new dispatch. Concurrency limits consider RUNNING attempts. One recommendation per invocation.
- No hidden filesystem/network/model calls, clock, credentials, runtime import, subprocess or private home path in the core.
- The local harness may write only a newly allocated private temporary directory, uses real G3 atomic apply and fresh independent readback, and stops at a verification request. Its in-memory callbacks are explicitly NOT durable stores or lifecycle promotion.
- Resume tests mean reevaluating reconstructed snapshots. They do NOT prove process-crash recovery, persistent outbox safety or exactly-once Agent execution.

## Required evidence

1. RED then GREEN tests for malformed DAG/snapshot, budget, dependency, concurrent-running limit, unknown, candidate and terminal projections.
2. Real local artifact creation and digest-bound receipt/verification; corrupted or missing artifact cannot request VERIFIED.
3. UNKNOWN replay makes zero effect calls. Installed package runs without Hermes or source-tree imports.
4. Wheel contents exclude private files, credentials, receipts and audit state.
5. One scoped independent complex-risk review; one targeted re-review available. Findings with reproduction evidence block this milestone.

## Still required for overall user goal

Hermes real execution adapter with submit/query/cancel/artifact contracts; separate second runtime conformance; canonical durable attempt/grant/evidence stores and crash reconciliation; bounded multi-step/replan budgets; full provenance clearance and release/license decision. No public release, push, deploy, activation or production certification is authorized by this spec.

## M2 — durable local adapter execution experiment (design revision 1)

Latest request: advance the original main task. Keep the r1 proposal-only package unchanged. Add a separately packaged `runtime/` experiment; this SPEC remains the only canonical requirement document. M2 implements adapter-attempt persistence, NOT ACP platform task/lifecycle ownership; no existing ACP package, live runtime, gateway, profile or scheduler mutations.

Success: executable stdlib-only adapter-attempt store and submit/query/cancel/artifact contract; a real local Python adapter and a version-bound isolated Hermes text-only adapter; fresh independent output verification; actual process-kill/reopen tests. Hermes integration is only accepted after a real existing-provider call, not a simulated response. If the runtime cannot be bounded without modifying live Hermes, record that boundary as unaccepted rather than use a mock.

Authority: callers are trusted local operators, not authenticated remote principals. An explicit local grant binds request SHA256, adapter identity and expiry; no grant, mismatch or expiry rejects execution before any process start. This models application authorization, NOT adversarial access control or an OS sandbox. Prompt text is non-secret and artifacts are caller-private. Operator-selected adapter code is trusted and never chosen by model output. No shell interpolation.

Store/identity: SQLite transactions persist immutable request identity, idempotency key, attempt, adapter/request digest, grant and state. Same key + different request rejects; exact repeated submit reads back same attempt. One task identity admits only one attempt in this milestone; a new key cannot replay an UNKNOWN task. Persisted budget/concurrency settings cannot silently change on reopen. Global start budget is transactionally bounded; RUNNING and UNKNOWN both retain a concurrency reservation until a separately authorized future resolution protocol exists. One conditional PREPARED→RUNNING claim permits at most one adapter call; no lease stealing. A process death after claim, timeout, interrupted execution or uncertain result becomes UNKNOWN and NEVER automatically launches again. A restarted query observes stored state without pretending an old RUNNING process is dead. Explicit reconcile converts uncertain RUNNING to UNKNOWN; future late completion cannot promote UNKNOWN. Cancelling PREPARED is terminal CANCELLED; cancelling an in-flight/unknown attempt is a reconciliation requirement, NOT proof the external worker stopped. No kill-by-PID after restart.

Evidence: each adapter result is a nonempty bounded UTF-8 text artifact written atomically in a private attempt directory and bound in SQLite by SHA256 plus request/attempt/adapter identities. Runner success stores CANDIDATE only. Separate verification reopens and hashes artifact and applies an operator-selected deterministic expected-text validator; only matching evidence records local VERIFIED. Missing, replaced, corrupt, wrong-identity, stale-attempt or mismatching output cannot become VERIFIED. No public arbitrary state setter or model-claimed VERIFIED. This local record does not promote ACP platform lifecycle and is not a general semantic evaluator. A crash before evidence commit stays UNKNOWN even if orphan files exist; no inference from file presence.

Execution bounds: timeout and output byte cap must be finite and validated before dispatch. Subprocess adapters run in a new process session with explicit argv and caller-private cwd. On timeout terminate and reap the owned process group, but retain UNKNOWN because termination is not an effect rollback. Reconcile never reruns. Tool-denial in the Hermes wrapper must cover final dispatch, concurrent paths, delegate and native provider execution, not just CLI flags. Pin wrapper/runtime identity, reject unsupported drift, suppress private logs and disable memory/context/checkpoints where the runtime supports them. Do not claim OS containment for trusted Python adapters.

Required negative and recovery evidence: no-grant/expired/wrong-digest makes zero adapter calls; duplicate and racing submit/start invoke at most once; failure/nonzero/invalid/oversized/empty/timeout cannot verify; artifact corruption/missing/cross-attempt evidence rejected; actual subprocess SIGKILL after RUNNING followed by a new process opening DB yields reconcile/UNKNOWN and no replay; accepted evidence survives fresh store open; Python and Hermes adapters exercise the same contract. The old r1 42 tests remain unchanged and passing.

Paths considered: extending ACP canonical owner is outside this isolated authorization; inventing a new platform lifecycle is rejected. Chosen: a narrow local adapter-attempt store with explicit projections to the existing proposal-only planner, preserving separate authority/execution/verification concerns. Risks retained: local operator trust, no exactly-once external effects, no malicious-binary sandbox, no durable adoption of orphan subprocesses, and no full semantic acceptance.

Review M2 as a new material execution-boundary scope: high-risk scoped design review, result review and at most one targeted re-review under one stable M2 review id. Prior r1 review remains limited to unchanged r1 code. No release or global certification claim.
