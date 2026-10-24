# Resolution v2 experiment

Implemented local experimental successor; result review and Alpha acceptance remain pending. Read SPEC.md and ../SPEC.md before use.

This opt-in experiment preserves the frozen v1 planner/runtime/composition/dataflow/replanning sources. It is not a migration and cannot open a v1 attempt root. The v1 Flow's exact-type guard does not accept this Store; a v2 host must explicitly orchestrate the public planner, attempt-owner and plan-journal interfaces. No type masquerading or global monkeypatch is supported.

The intended boundary is trusted synchronous local POSIX execution of operator-selected programs that do not detach from their owned process group. It is not a hostile-program sandbox, authentication service, remote scheduler or external side-effect rollback mechanism.

ResolutionGrant is distinct from execution Grant and plan Approval. Resolution only records FAILED after durable closure evidence and bound authorization; it never produces VERIFIED or ACP task completion. An orphaned runner that died before recording closure stays UNKNOWN, continues to occupy its slot, and is not automatically replayed or killed from a saved PID.

The success criterion is a genuinely failed process, explicit resolution retaining UNKNOWN history and spent budget, approved new task, separately granted repaired execution, and fresh artifact verification. Python/Node.js process probes are language-runtime tests, not proof of integrating a second AI Agent runtime. Real ACP owner completion, second Agent integration, unified product installation and sustained real use are separate user-approved work, not implied by this package.

No release, deployment, live Hermes configuration change or existing database mutation is authorized by running the tests. Test PostgreSQL, where used later, must be a newly created disposable fixture.

Provenance: the new Store and process adapter are a version-isolated successor of this repository's experiments/conductor/runtime source; Request and Grant reuse its public frozen package. Apache-2.0 repository/runtime license applies; no third-party orchestration source is introduced here. The publishing rights holder has confirmed Apache-2.0 redistribution rights for the additions and derivative modifications; see the distributed PROVENANCE.md. This does not approve the separate product-result review.
