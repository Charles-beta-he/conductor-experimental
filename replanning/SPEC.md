# M5 — bounded plan revision and cumulative proposal budgets (design R1)

Parent objective: ../SPEC.md. This is the next local experiment, not a replacement definition of
runtime-independent coordination. M4 is under result review and remains frozen. No live gateway,
profile, scheduler, ACP lifecycle, deployment, publication or external model call is authorized here.

## Objective / chosen path

Replace the per-call-only replanning gap with a separately packaged stdlib-only plan-admission
journal: persist a current DAG revision, plan history and cumulative proposal/revision counters.
It owns ONLY plan metadata. M2 remains sole owner of attempts/grants/start limits/evidence; an
existing ACP owner retains platform lifecycle. No new worker queue, lease adoption or outcome setter.
A pure function alone cannot prove cumulative budgets across restart; rewriting M2/M3 is rejected.
Choose a narrow SQLite plan journal + explicit host composition over unchanged public APIs.

## Scope and success criteria

1. Trusted single synchronous local host; fixed journal path and immutable caps. Reopen cannot
   change caps or reset spent rounds/revisions. Initial plan counts as revision 0. Strict positive
   finite integer caps for proposal rounds, plan revisions and graph size (at most 256 tasks).
   No token/cost/wallclock budget claim: a round is one reserved proposal-generation opportunity.
2. Initial tasks and every proposed full DAG use canonical Task validation. Task identities cannot
   be reused for different dependencies after adoption, even across retired revisions. No cycle,
   unknown dependency, duplicate identity, loose JSON type or unbounded model field is accepted.
3. Host must reserve a round transactionally BEFORE any proposal callback/tool is invoked. Every
   reservation permanently spends one round even if callback fails, crashes or produces bad output.
   At most one unresolved reservation. Reserve is idempotent only for the identical round key;
   it returns existing state rather than indicating permission to re-invoke a callback. A claimed
   round after process death is uncertain: no automatic retry/replay, no new reservation/adoption.
   Explicit host reconciliation may close it UNKNOWN without refund, permitting a later distinct
   round within the remaining cap. UNKNOWN here is a proposal-round outcome, not an M2 state write.
4. Proposal text is untrusted data. Parse ONLY a bounded JSON envelope containing a DAG proposal;
   reject duplicate/extra fields, invalid state/type, code/adapter/grant/expected/lifecycle fields,
   non-UTF-8, oversized bytes, NaN and trailing content. Proposal cannot grant, submit, run, verify,
   cancel, reconcile M2, change caps or mutate current plan. A failed proposal still consumes its round.
5. Adoption is a separate trusted-host operation, requiring exact current-revision digest and exact
   candidate digest plus explicit local approval with expiry. Approval is NOT an execution Grant.
   Old/mismatched/expired approval, stale base or exhausted revision budget yields no new revision.
   Identical adoption returns same revision without spending twice; different candidate on same
   adoption key rejects. Reopened readback must preserve canonical candidate/plan bytes and identity.
6. Adoption also consumes a complete fresh host projection of ALL historically bound attempts,
   including retired tasks. Host-supplied completeness is trusted, not authenticated. The journal
   records task/attempt identities once via explicit host binding; it never adopts alternate
   attempts for an existing task or loses historical identities through a new revision. Every
   recorded attempt must be supplied and must match its unchanged task identity at adoption.
   UNKNOWN or RUNNING anywhere blocks adoption (unknown requires reconciliation; running waits).
   PREPARED/CANDIDATE tasks cannot be silently dropped or structurally changed; VERIFIED retained
   tasks preserve dependencies; FAILED/CANCELLED may be explicitly retired from the new DAG, but
   are never reset/retried under the same task identity. New work needs a distinct task identity
   and separate exact M2 Grant. A newly invalid upstream (UNKNOWN projection) also blocks adoption.
7. M2's immutable global max_starts/max_running remain cumulative across ALL revisions and
   fresh processes. M5 does not fabricate starts from observation count or claim a second counter
   is authoritative. PREPARED/unstarted CANCELLED consume zero starts; UNKNOWN retains M2's slot.
   Host's execution path continues to pass M2's exact request/grant gate; plan approval never bypasses it.
8. Read-only plan/round history is not a lifecycle acceptance certificate. No direct adapter calls,
   process creation, network, credential lookup or hidden model selection in the journal package.
   Actual proposal generation and execution remain explicit, separately authorized host stages.

## Required result evidence

- RED/GREEN negative tests for input, caps, proposal schema, exact approval, stale base, duplicate
  idempotency, forbidden history removal and state projections; transactional race tests for caps.
- Real fresh-process reserve/offer/read/adopt sequence, identical IDs/digests after reopen; kill a
  process after reservation and prove zero proposal replay, no refund and explicit UNKNOWN closure.
- Actual failed local Python attempt -> untrusted proposal bytes from a separate Python process ->
  host-approved revised plan with a distinct task -> separately granted real execution -> independent
  evidence verification. This proves host replanning composition, NOT a live LLM planning call.
- Exhausted M2 start budget across a revision/reopen blocks the new adapter with zero extra starts.
  UNKNOWN attempt blocks replan/execution, including when its task is absent from the candidate DAG.
- Fresh wheel installation without Hermes/ACP source imports; source=wheel=installed bytes;
  old frozen r1/M2/M3/M4 identities and regression tests unchanged.
- High-risk bounded review: design + result + at most one targeted re-review under stable id
  conductor-extraction-m5-replanning-v1. This spec is currently DESIGN ONLY, not implementation.

## Retained boundaries

Not a platform lifecycle owner, authenticated remote approval service, adversarial same-UID store,
transactional commit across plan-journal and M2 SQLite databases, full autonomous replanner, semantic
verifier, wallclock/cost governor or real cross-runtime LLM loop. Cross-store interruption must stop
at explicit readback/reconciliation, never create a replacement attempt or infer execution success.
Owner/ACP integration and overall cross-runtime delivery remain separate mainline acceptance work.
