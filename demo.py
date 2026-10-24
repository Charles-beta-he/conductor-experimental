"""Explicit disposable local-file composition, not a runtime execution adapter.

Uses installed ACP public APIs. Receipt/verification lists are non-durable demo
callbacks. No lifecycle promotion, Hermes, network, subprocess or arbitrary path.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import acp_local_file_effect as effect
import acp_local_file_verifier as verifier

from acp_conductor_experiment import Attempt, Task, decide


def demonstrate(scenario: str = "match") -> dict:
    if scenario not in {"match", "corrupt", "missing", "unknown"}:
        raise ValueError("unsupported demonstration scenario")
    tasks = (Task("artifact"),)
    used = 0

    def recommendation(observations=()):
        return decide(tasks, observations, attempts_used=used, max_attempts=1, max_running=1)

    if scenario == "unknown":
        used = 1
        decision = recommendation((Attempt("artifact", "demo:attempt", "UNKNOWN"),))
        return {
            "scenario": scenario,
            "recommendation": decision.kind,
            "effect_calls": 0,
            "lifecycle_promoted": False,
            "durable_store": False,
        }
    before = recommendation()
    assert before.kind == "dispatch_proposal"
    # This explicit harness invocation authorizes only its own newly created root;
    # the planner proposal is NOT being treated as an AuthorityGrant.
    with TemporaryDirectory(prefix="acp-conductor-demo-") as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        identity = root.stat()
        body = b"Conductor local-file composition evidence\n"
        target = effect.admit_local_file_target(
            root=str(root), root_device=identity.st_dev, root_inode=identity.st_ino,
            target_name="artifact.bin", content_kind="bytes",
        )
        payload = effect.bind_local_file_payload(
            target, body, "sha256:" + hashlib.sha256(b"disposable-conductor-demo-v1").hexdigest()
        )
        applied = effect.apply_local_file_payload(payload)
        used = 1
        observation = effect.observe_local_file_payload(payload)
        receipts = []
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        receipt_outcome = effect.record_local_file_execution_receipt(
            applied, observation, payload,
            attempt_id="demo:attempt", request_id="demo:request",
            adapter_id="demo:local-file", receipt_id="demo:receipt",
            emitted_at=now, sequence=0,
            append_receipt=lambda receipt: receipts.append(receipt) or "not_executed",
        )
        assert receipt_outcome.receipt_recorded
        candidate = recommendation((Attempt("artifact", "demo:attempt", "CANDIDATE"),))
        assert candidate.kind == "verify_request"
        artifact = root / "artifact.bin"
        if scenario == "corrupt":
            artifact.write_bytes(b"corrupted after execution")
        elif scenario == "missing":
            artifact.unlink()
        records = []
        outcome = verifier.verify_recorded_local_file_receipt(
            receipts[0], payload,
            verification_id="demo:verification", verifier_id="demo:independent-verifier",
            checked_at=now, sequence=0,
            read_receipt=lambda receipt_id: receipts[0] if receipt_id == "demo:receipt" else None,
            read_observation=lambda _locator: effect.observe_local_file_payload(payload),
            append_verification=lambda record: records.append(record) or "not_executed",
        )
        # Do NOT manufacture VERIFIED input: no Attempt Store promoted this task.
        after = recommendation((Attempt("artifact", "demo:attempt", "CANDIDATE"),))
        return {
            "scenario": scenario,
            "before": before.kind,
            "after": after.kind,
            "apply_result": applied,
            "artifact_sha256": hashlib.sha256(body).hexdigest(),
            "readback_verdict": outcome.record.verdict,
            "verification_requested_state": outcome.requested_state,
            "effect_calls": 1,
            "lifecycle_promoted": False,
            "durable_store": False,
        }


if __name__ == "__main__":
    print(json.dumps([demonstrate(s) for s in ("match", "corrupt", "missing", "unknown")], indent=2))
