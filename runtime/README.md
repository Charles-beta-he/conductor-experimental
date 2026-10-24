# M2 local adapter-attempt runtime experiment

This is a separately installable, stdlib-only experiment, not ACP platform lifecycle,
a daemon, an authenticated remote service or an OS sandbox. See `../SPEC.md` M2 for
the canonical requirements. The proposal-only r1 package is unchanged.

## Run offline

From this directory, using the parent lab's disposable development environment:

    ../.venv/bin/python -m pip install -e .
    ../.venv/bin/python -m pytest -o addopts='' -q
    ../.venv/bin/python -m ruff check src tests demo.py
    ../.venv/bin/python demo.py --evidence-parent /absolute/private/evidence-parent

The demo prints the durable receipt path. The parent is operator-selected. Each run
allocates a new private directory; it does not reuse or overwrite previous evidence.
Python source package is `acp_conductor_runtime`. Core imports do not import Hermes.

Optional live Hermes demo (explicit paths, provider and model required):

    ../.venv/bin/python demo.py --evidence-parent /absolute/private/evidence-parent \
      --hermes-python /absolute/hermes/venv/bin/python \
      --hermes-root /absolute/installed-hermes-source \
      --provider openai-codex --model YOUR_EXISTING_MODEL

Only the local installed Hermes revision exercised in the private M2 evidence is
accepted by an **external operator admission gate**, not an executable revision
allowlist. The constructor does not automatically reject unsupported revisions.
A runtime fingerprint binds argv, worker, bridge and selected Hermes
Python source files; drift of pinned files stops execution. Its roster is fixed at
construction: newly added modules are not detected. Runtime upgrades/additions need
fresh operator admission and verification, not reuse of an old identity.
This is not a portable
Hermes SDK or a pin of all third-party dependencies, binaries or OS environment.
Existing credentials are read inside the child, not passed in argv or receipts.
Codex refresh, CLI auth recovery and auth-store writes are refused; config is read
through Hermes's readonly loader. Missing/expired credentials require operator
repair outside this experiment. No credential entry or login flow is provided.
The child then switches to a private Hermes home in the attempt directory. Incidental
runtime caches/selfcheck files may exist there; they are not product receipts.
Provider calls consume the existing provider quota; offline tests never call a model.

## State and recovery

`submit(key, request, grant)` binds immutable identity and a finite local grant.
`run(attempt, adapter)` durably claims PREPARED -> RUNNING before invoking the adapter.
It rechecks the persisted grant before the adapter call. Subprocess/Hermes adapters
consume it through `execute_authorized(request, workspace, grant)` and check again
after pin/stdin preparation immediately before Popen. Authorization linearizes at
that final check; OS scheduling and child entry are not promised before expiry.
Expiry after claim leaves UNKNOWN and never retries or refunds the start claim.
The original `execute(request, workspace)` remains a direct trusted-code interface,
not an authorized Store execution. Custom trusted adapters retaining only `execute`
receive the Store pre-call check; any delayed effects they own need their own final
authorization gate (they may implement `execute_authorized` to consume the grant).
Success yields CANDIDATE only. `artifact` reopens bounded regular-file bytes;
`verify` independently reopens, checks request/path/digest and deterministic expected
text before recording local VERIFIED. This does not attest general task semantics.
`query` conservatively projects invalidated VERIFIED artifacts to UNKNOWN.
`cancel` before start yields CANCELLED; after start yields UNKNOWN and does NOT claim
external termination. `reconcile` marks RUNNING unknown; it never replays or adopts a
PID. A late return cannot promote UNKNOWN. UNKNOWN retains its concurrency slot;
there is deliberately no release/retry/reset mechanism in M2.

One task admits one attempt. Matching idempotency keys return that attempt; another
key cannot replay the same task. Start and concurrency budgets are persisted and
cannot silently change on reopen. A restarted query alone cannot infer whether the
old process died. Operator reconciliation is explicit. Orphan files are not evidence
of committed success. Parent SIGKILL does not promise that a detached child stopped.
Timeout/output-cap cleanup kills only the process group created by that invocation.

Trust boundary: operators, selected adapter code, the local filesystem and DB are
trusted. Grants are local application checks, not cryptographic authentication.
Same-UID adversarial DB editing or filesystem replacement and subprocess escape are
outside this model. No global monkeypatch or change to the installed runtime is used:
Hermes hooks are overridden only in the isolated child. Model tool/native/delegate
paths and provider-native payloads are denied. This is defense in depth, not an OS
sandbox or proof that arbitrary future Hermes revisions are compatible.

No publish, push, activation, gateway/scheduler change, ACP state promotion or
production acceptance is part of this package. Tests and private evidence are separate
from wheel contents. The overall multi-step coordination product remains unfinished.
