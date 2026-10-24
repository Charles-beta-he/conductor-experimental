"""Explicit local demo; optional live Hermes call uses existing provider credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

from acp_conductor_runtime import Grant, Request, Store, SubprocessAdapter
from acp_conductor_runtime.hermes import HermesAdapter


def demonstrate(adapter, root: Path):
    req = Request("demo", adapter.identity, "Reply with exactly CONDUCTOR_M2_OK and nothing else.",
                  "CONDUCTOR_M2_OK", timeout=90, max_output=8192)
    store = Store(root)
    attempt = store.submit("one-shot", req, Grant(req.digest, req.adapter_id, time.time() + 120))
    candidate = store.run(attempt, adapter)
    verified = Store(root).verify(attempt)
    result = {"attempt": attempt, "request_digest": req.digest, "adapter_id": adapter.identity,
              "candidate_state": candidate["state"], "verified_state": verified["state"],
              "reason": verified["reason"], "store": str(root)}
    if verified["state"] == "VERIFIED":
        output = Store(root).artifact(attempt)
        result["text"] = output.decode()
        result["artifact_sha256"] = hashlib.sha256(output).hexdigest()
        result["repeat_state"] = Store(root).run(attempt, adapter)["state"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-parent", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path)
    parser.add_argument("--hermes-root", type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    args = parser.parse_args()
    args.evidence_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="m2-", dir=args.evidence_parent))
    local = SubprocessAdapter((sys.executable, "-c", "print('CONDUCTOR_M2_OK')"))
    results = {"python": demonstrate(local, run / "python")}
    if args.hermes_python:
        if not all((args.hermes_root, args.provider, args.model)):
            parser.error("Hermes requires explicit root, provider and model")
        adapter = HermesAdapter(args.hermes_python, args.hermes_root,
                                provider=args.provider, model=args.model)
        results["hermes"] = demonstrate(adapter, run / "hermes")
    receipt = run / "receipt.json"
    receipt.write_text(json.dumps(results, indent=2) + "\n")
    receipt.chmod(0o600)
    print(json.dumps({"receipt": str(receipt), "results": results}, indent=2))
    return 0 if all(x["verified_state"] == "VERIFIED" for x in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
