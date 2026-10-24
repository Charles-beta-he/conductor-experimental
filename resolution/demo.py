"""Actual nonzero -> UNKNOWN -> authorized FAILED -> admitted real repair.

Run only with a new local evidence directory. No model/network/platform call.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from acp_conductor_experiment import Task
from acp_conductor_replanning import Approval, Journal

from acp_conductor_resolution import Grant, Request, ResolutionGrant, Store, SubprocessAdapter
from acp_conductor_resolution.host import Host


def main(root):
    root.mkdir(exist_ok=False, parents=True)
    store = Store(root / "attempts", max_starts=2)
    journal = Journal(root / "plan.db", initial=(Task("broken"),), max_rounds=1, max_revisions=1)
    host = Host(store, journal)
    broken = SubprocessAdapter((sys.executable, "-I", "-c", "raise SystemExit(7)"))
    request = Request("broken", broken.identity, "repair a failed local process", "REPAIRED")
    result = host.step(
        "first", request, Grant(request.digest, request.adapter_id, time.time() + 60), broken
    )
    assert result["state"] == "UNKNOWN"
    journal.reserve("repair")
    proposal = journal.offer(
        "repair", b'{"version":1,"tasks":[{"task_id":"repaired","dependencies":[]}]}'
    )
    approval = Approval(proposal["base_digest"], proposal["candidate_digest"], time.time() + 60)
    rejected = False
    try:
        host.adopt("approve", "repair", approval)
    except ValueError:
        rejected = True
    assert rejected
    evidence = store.resolution_evidence("first")
    resolution = ResolutionGrant(
        "first", request.digest, evidence["evidence_digest"], time.time() + 60
    )
    store.resolve("first", resolution)
    host.adopt("approve", "repair", approval)
    good = SubprocessAdapter((sys.executable, "-I", "-c", "print('REPAIRED')"))
    repaired = Request("repaired", good.identity, "new task, new execution grant", "REPAIRED")
    result = host.step(
        "second", repaired, Grant(repaired.digest, repaired.adapter_id, time.time() + 60), good
    )
    assert result["state"] == "VERIFIED"
    assert host.step().kind == "accepted_projection"
    report = {
        "root": str(root),
        "uncertain_adoption_rejected": rejected,
        "failed_history": store.history("first"),
        "repair_history": store.history("second"),
        "states": [store.query(a)["state"] for a in ("first", "second")],
        "stats": store.stats(),
        "artifact": store.artifact("second").decode(),
        "plan": journal.snapshot(),
        "scope": "local attempt owner; not ACP task completion",
    }
    (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    main(parser.parse_args().root.resolve())
